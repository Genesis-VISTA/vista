"""
Trust scorer and tier policy for VISTAGuard.

This module establishes the *public API* that gates will use to record
security signals during Phases 1-4. It deliberately ships with
intentionally-simple Phase-0 scoring logic that is enough to be useful
(score moves in response to signals, gates can read the current state)
but not yet correct. The real scoring logic -- per-capability Bayesian
credit assignment, sticky high-stakes tier lock-in, SEV2-triggered
forced re-authentication -- arrives in Phase 5 (see the integration
plan §2 Phase 5 and the work-items doc).

The reason for this split is API stability. Phase 5 will rewrite the
internals of `TrustScorer.record_violation` and `record_clean_call`
to do per-capability credit assignment. If those methods don't exist
in Phase 0, every gate written between Phases 1 and 4 either has no
trust feedback at all (so the gates are missing the most important
signal-generation behavior) or has its own ad-hoc scoring that gets
ripped out in Phase 5. Shipping the *API* in Phase 0 means gates
written in Phases 1-4 just keep working when the scoring is
upgraded.

## Phase-0 scope

What this module does in Phase 0:

- Track a single global score in [0.0, 1.0].
- Decrement on `record_violation(severity)` by an amount proportional
  to severity; clamp to [0.0, 1.0].
- Increment on `record_clean_call()` by a small fixed amount; clamp
  to [0.0, 1.0].
- Always report `TrustTier.NORMAL` regardless of score (no tier
  transitions in Phase 0; that's Phase 5 work).
- Accept and store -- but do nothing with -- a `capability_kind`
  argument on `record_violation`. Phase 5's Bayesian per-capability
  scorer will read this.
- No-op when `settings.vistaguard.enabled` is False: violations and
  clean calls are accepted but the score stays at `initial_trust`.

What this module does *not* do in Phase 0:

- Tier transitions (NORMAL -> ELEVATED -> RESTRICTED -> TERMINATED).
- Sticky high-stakes capability lock-in (a denied G6-destructive
  capability staying denied even after the score recovers).
- Per-capability Bayesian credit assignment.
- SEV2 triggering forced re-authentication.
- Integration with the incident manager (the incident-emitted -> trust-
  scorer-notified path is wired in Phase 5 once `IncidentManager`
  exists).

All five of those are Phase 5 issues with their own acceptance
criteria.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
import logging
import math

from .capabilities import TrustTier
from .config import VistaGuardSettings


logger = logging.getLogger(__name__)


# -----------------------------------------------------------------
# Phase-0 scoring constants
# -----------------------------------------------------------------
#
# These constants encode "any signal moves the score, but the
# magnitudes are not yet tuned." Phase 5 replaces the entire
# update rule with a per-capability Bayesian model, so the values
# here only need to be (a) signed correctly (violations decrement,
# clean calls increment), (b) small enough that a single signal
# doesn't saturate the score, and (c) in a sensible ratio so that
# tests can verify the score actually moves.
#
# Reviewers: do *not* spend time tuning these values. They will be
# replaced wholesale in Phase 5.

_VIOLATION_DECREMENT_BY_SEVERITY: dict[int, float] = {
    3: 0.05,   # SEV3: informational; small dent
    2: 0.15,   # SEV2: warning
    1: 0.40,   # SEV1: severe
}

_CLEAN_CALL_INCREMENT: float = 0.01

_VALID_SEVERITIES = frozenset(_VIOLATION_DECREMENT_BY_SEVERITY.keys())


# -----------------------------------------------------------------
# Violation history record
# -----------------------------------------------------------------


@dataclass(frozen=True)
class _ViolationRecord:
    """
    A single violation event, stored on the scorer for Phase-5
    Bayesian credit assignment. Phase-0 code does not read these
    records beyond `len(history)` and `last_violation`; they exist
    so Phase 5 has a real history to compute posteriors from
    without a separate migration.

    `capability_kind` is the value passed to
    `TrustScorer.record_violation(..., capability_kind=...)`. Phase 0
    accepts any string; Phase 5 will likely tighten this to a set
    of known capability names ("tool_call", "rag_retrieve",
    "hpc_submit", "instrument_command", etc.).
    """

    severity: int
    capability_kind: str | None
    score_before: float
    score_after: float


# -----------------------------------------------------------------
# TierPolicy
# -----------------------------------------------------------------


class TierPolicy:
    """
    Maps a trust score to a `TrustTier`.

    Phase 0: always returns `TrustTier.NORMAL` regardless of input,
    by design. Phase 5 will replace this implementation with one that
    reads `settings.tier_transition_thresholds` and applies the
    sticky high-stakes capability lock-in described in main proposal
    §4 C4 and §8.4.

    The class is split from `TrustScorer` so that Phase 5 can
    introduce per-capability tier policies (e.g., the score gates
    different capabilities at different thresholds) without churning
    the scorer's API.
    """

    def __init__(self, settings: VistaGuardSettings) -> None:
        self._settings = settings

    def tier_for_score(self, score: float) -> TrustTier:
        """
        Return the tier for a given score.

        Phase 0: always NORMAL. The argument is accepted (and a tiny
        amount of validation is done, since Phase 5 will care about
        out-of-range scores) so that gates calling this method now
        get a stable signature.
        """
        if not (0.0 <= score <= 1.0):
            # A score outside [0, 1] is a logic bug somewhere; Phase
            # 0 logs and returns NORMAL anyway to avoid masking real
            # behavior with a hard failure. Phase 5 may tighten this
            # to a ValueError once the scoring contract is firm.
            logger.warning(
                "TierPolicy.tier_for_score got out-of-range score %r; "
                "Phase 0 returns NORMAL regardless.",
                score,
            )
        return TrustTier.NORMAL


# -----------------------------------------------------------------
# TrustScorer
# -----------------------------------------------------------------


class TrustScorer:
    """
    Per-session trust scorer.

    Holds a single floating-point score in [0.0, 1.0] that gates
    update via `record_violation` and `record_clean_call`. The
    current score and tier are read via the `current_score` property
    and `current_tier()` method.

    Phase-0 behavior is intentionally simple (single global score,
    no tier transitions). Phase 5 will rewrite the internals; the
    public API on this class is the stable interface that gates
    written in Phases 1-4 depend on.

    Not thread-safe by design -- see `CapabilityRegistry`'s
    docstring for the rationale (VISTA's agent loop runs each
    `ProjectAgent.run_stream` in a single async task, so
    single-task access is sufficient).
    """

    def __init__(
        self,
        settings: VistaGuardSettings,
        policy: TierPolicy | None = None,
    ) -> None:
        self._settings = settings
        self._policy = policy if policy is not None else TierPolicy(settings)
        self._score: float = self._clamp(settings.initial_trust)
        self._history: list[_ViolationRecord] = []

    # -----------------------------------------------------------------
    # Read-only state
    # -----------------------------------------------------------------

    @property
    def current_score(self) -> float:
        """The current trust score, in [0.0, 1.0]."""
        return self._score

    @property
    def policy(self) -> TierPolicy:
        """The `TierPolicy` instance backing `current_tier()`."""
        return self._policy

    @property
    def history(self) -> Sequence[_ViolationRecord]:
        """
        Read-only view of recorded violations. Returned as a
        `Sequence` so callers can't append. Phase 5's Bayesian
        scorer will read this; Phase-0 gates should treat it as
        opaque.
        """
        return tuple(self._history)

    @property
    def last_violation(self) -> _ViolationRecord | None:
        """The most recent violation, or None if none recorded."""
        return self._history[-1] if self._history else None

    def current_tier(self) -> TrustTier:
        """
        Return the current `TrustTier`. Phase 0: always NORMAL.

        This is a method rather than a property so that Phase 5's
        per-capability variant -- `current_tier_for(capability)` --
        can coexist on the same class without a property/method
        type mismatch.
        """
        return self._policy.tier_for_score(self._score)

    # -----------------------------------------------------------------
    # Signal recording
    # -----------------------------------------------------------------

    def record_violation(
        self,
        severity: int,
        *,
        capability_kind: str | None = None,
    ) -> None:
        """
        Record a security violation at the given severity.

        `severity` is in {1, 2, 3} matching the SEV1/SEV2/SEV3
        convention from the main proposal:

        - 3 (SEV3): informational. Small score decrement.
        - 2 (SEV2): warning. Larger decrement; in Phase 5 this
          triggers tier elevation and forced re-authentication.
        - 1 (SEV1): severe. Largest decrement; in Phase 5 this
          terminates the session.

        `capability_kind` is a Phase-5 hook. Phase 0 records the
        value in the violation history but does not use it to
        adjust scoring. Phase 5's Bayesian scorer will use it for
        per-capability credit assignment.

        No-op when the master flag is off: the violation is logged
        at DEBUG and ignored, leaving the score and history
        unchanged. This means gates can call this unconditionally
        without checking the enabled flag.

        Raises `ValueError` for an unrecognized severity. This is
        a programming error, not a runtime condition, so failing
        loudly is appropriate.
        """
        if severity not in _VALID_SEVERITIES:
            raise ValueError(
                f"severity must be one of {sorted(_VALID_SEVERITIES)}; "
                f"got {severity!r}"
            )

        if not self._settings.enabled:
            logger.debug(
                "TrustScorer.record_violation ignored (enabled=False): "
                "severity=%d capability_kind=%r",
                severity,
                capability_kind,
            )
            return

        before = self._score
        decrement = _VIOLATION_DECREMENT_BY_SEVERITY[severity]
        self._score = self._clamp(self._score - decrement)
        self._history.append(
            _ViolationRecord(
                severity=severity,
                capability_kind=capability_kind,
                score_before=before,
                score_after=self._score,
            )
        )
        logger.info(
            "TrustScorer: SEV%d violation (capability_kind=%r); "
            "score %.3f -> %.3f",
            severity,
            capability_kind,
            before,
            self._score,
        )

    def record_clean_call(self) -> None:
        """
        Record a clean (non-violating) operation.

        Increments the score by a small fixed amount. No-op when
        the master flag is off, like `record_violation`.

        Phase 5 will replace this with per-capability Bayesian
        credit assignment (clean tool calls restore tool-call
        capability, but not RAG capability, etc., per the main
        proposal §4 C4).
        """
        if not self._settings.enabled:
            return
        before = self._score
        self._score = self._clamp(self._score + _CLEAN_CALL_INCREMENT)
        if self._score != before:
            logger.debug(
                "TrustScorer: clean call; score %.3f -> %.3f",
                before,
                self._score,
            )

    def reset(self) -> None:
        """
        Reset the scorer to `settings.initial_trust` and clear the
        violation history.

        Mostly useful for tests; production code creates a new
        `TrustScorer` per session rather than resetting an
        existing one. Phase 5 will gain a re-authentication
        mechanism that clears the sticky high-stakes lock-in;
        `reset()` is not that mechanism.
        """
        self._score = self._clamp(self._settings.initial_trust)
        self._history.clear()

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    @staticmethod
    def _clamp(value: float) -> float:
        """
        Clamp a value to [0.0, 1.0], with NaN treated as 0.0.

        NaN handling matters because Phase 5's Bayesian update may
        produce NaN under degenerate inputs, and a NaN score would
        propagate silently through every subsequent comparison.
        Phase 0 doesn't produce NaN itself, but defends against it
        so the contract holds when Phase 5 plugs in.
        """
        if math.isnan(value):
            return 0.0
        if value < 0.0:
            return 0.0
        if value > 1.0:
            return 1.0
        return value