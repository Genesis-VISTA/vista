"""
Trust scorer and tier policy for VISTAGuard.

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

    def notify_incident(self, *, level: int, gate: str) -> None:
        """
        Receive an incident notification from `IncidentManager`.

        Phase-0 stub: no-op (the master-flag check is performed
        even so, so that the call site logs intent at DEBUG when
        disabled). Phase 5 will fill in the body to drive tier
        transitions:

        - SEV1 (level=1): transition to TERMINATED.
        - SEV2 (level=2): transition to ELEVATED and set a
          re-auth-required flag.
        - SEV3 (level=3): record for trend analysis only.

        This method exists in Phase 0 so `IncidentManager.record(...)`
        can call it unconditionally on the wired-in trust scorer.
        Adding the stub here lets Phases 1-4 produce real incidents
        and have them propagate to the scorer once Phase 5 fills in
        the playbook.

        `level` validation is done by `IncidentManager.record()`
        upstream; we trust the caller to pass a valid value.
        """
        if not self._settings.enabled:
            logger.debug(
                "TrustScorer.notify_incident ignored (enabled=False): "
                "level=%d gate=%s",
                level,
                gate,
            )
            return
        logger.debug(
            "TrustScorer.notify_incident received SEV%d from %s "
            "(Phase-0 stub; tier transitions arrive in Phase 5)",
            level,
            gate,
        )

    def reset(self) -> None:
        """
        Reset the scorer to `settings.initial_trust` and clear the
        violation history.
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