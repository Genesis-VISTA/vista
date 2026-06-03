"""
VISTAGuard trust scorer and tier policy (Phase 5).

This module holds the per-session trust state as plain Python on the
sidecar -- the "capabilities live in the interpreter, not the LLM
context" principle from the proposal. Nothing here calls a model or
does I/O.

## Per-capability Bayesian credit assignment

The Phase-0 scorer kept a single session-wide float. Phase 5 replaces
it with one ``Beta(alpha, beta)`` posterior *per capability kind* (the
gate that produced the signal: ``"G1"``..``"G5"``, or any caller-chosen
label). Every signal updates exactly one capability's posterior:

  * a clean call adds success mass to that capability's ``alpha``;
  * a violation adds failure mass to that capability's ``beta``, scaled
    by the event's severity and whether it is high-stakes.

Because the posteriors are independent, an oscillation attacker who
trips the RAG gate (G3) cannot wash out the breach by running a stream
of clean tool calls (G2): clean G2 calls only ever touch G2's posterior.
This is the structural property acceptance-tested in
``test_trust_bayesian.py``.

A capability's tier is derived from its posterior mean via the
configured thresholds (`TierPolicy`). The session-level tier
(`current_tier`) is the *worst* tier across all capabilities, so a
single breached boundary degrades the whole session.

## Sticky high-stakes 

A high-stakes violation ratchets a per-capability *floor* tier that the
capability can never climb back above within the session, even if later
events are benign. This defeats the probe-then-strike oscillation
attack where an adversary alternates good/bad behavior to recover a
breached high-stakes capability. The floor is cleared only by
`reset()` (a full session reset); explicit re-authentication is wired
in a later issue. When ``settings.sticky_high_stakes`` is False (used
only for the oscillation benchmark) the floor is not applied and tiers
recover normally.

## Backwards-compatible surface

The Phase-0 API that gates written in Phases 1-4 depend on is
preserved unchanged:

  * `record_violation(severity, *, capability_kind=...)`
  * `record_clean_call(...)`
  * `notify_incident(*, level, gate)`  -- called by `IncidentManager`
  * `current_tier()`, `current_score`, `reset()`
  * `TierPolicy`, `policy`, `history`, `last_violation`

With no events (or only clean events) and the default
``initial_trust=1.0``, every capability mean is 1.0 and the tier is
NORMAL -- byte-for-byte the Phase-0 idle behavior.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from threading import RLock

from .capabilities.registry import TrustTier
from .config import VistaGuardSettings

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------
# Scoring constants
# -----------------------------------------------------------------

# Per-severity base failure magnitude, carried over from the Phase-0
# decrements so the relative weighting of SEV1/SEV2/SEV3 is unchanged.
# Phase 5 multiplies these by `trust_failure_scale` to turn them into
# Beta failure mass.
_VIOLATION_MAGNITUDE_BY_SEVERITY: dict[int, float] = {
    3: 0.05,   # SEV3: informational; small dent
    2: 0.15,   # SEV2: warning
    1: 0.40,   # SEV1: severe
}

_VALID_SEVERITIES = frozenset(_VIOLATION_MAGNITUDE_BY_SEVERITY.keys())

# Defaults for the Bayesian knobs. Read off `VistaGuardSettings` via
# `getattr` so a deployment can override them without this module
# requiring new config fields to exist.
_DEFAULT_PRIOR_STRENGTH = 4.0       # pseudo-observations in the prior
_DEFAULT_SUCCESS_WEIGHT = 1.0       # alpha mass per clean call
_DEFAULT_FAILURE_SCALE = 5.0        # beta mass per unit of magnitude
_DEFAULT_HIGH_STAKES_MULT = 2.0     # extra beta mass for high-stakes


# Tier severity ordering (NORMAL is best, TERMINATED is worst). Used to
# ratchet the sticky floor and to take the worse of two tiers.
_TIER_RANK: dict[TrustTier, int] = {
    TrustTier.NORMAL: 0,
    TrustTier.ELEVATED: 1,
    TrustTier.RESTRICTED: 2,
    TrustTier.TERMINATED: 3,
}


def _worse(a: TrustTier, b: TrustTier) -> TrustTier:
    """Return the more severe (higher-rank) of two tiers."""
    return a if _TIER_RANK[a] >= _TIER_RANK[b] else b


def _clamp(value: float) -> float:
    """
    Clamp a value to [0.0, 1.0], with NaN treated as 0.0.

    NaN handling matters because a degenerate Beta update could in
    principle produce NaN, and a NaN score would propagate silently
    through every subsequent tier comparison.
    """
    if math.isnan(value):
        return 0.0
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


# -----------------------------------------------------------------
# Violation history record
# -----------------------------------------------------------------


@dataclass(frozen=True)
class _ViolationRecord:
    """A single violation event, retained for audit/tests."""

    severity: int
    capability_kind: str | None
    high_stakes: bool
    score_before: float
    score_after: float


# -----------------------------------------------------------------
# Per-capability posterior
# -----------------------------------------------------------------


@dataclass
class _CapabilityPosterior:
    """Per-capability Beta posterior over how trustworthy a boundary
    has been this session."""

    alpha: float
    beta: float
    floor: TrustTier | None = None
    sticky: bool = False
    clean_calls: int = 0
    violations: int = 0

    @property
    def mean(self) -> float:
        total = self.alpha + self.beta
        return _clamp(self.alpha / total) if total > 0 else 1.0


# -----------------------------------------------------------------
# TierPolicy
# -----------------------------------------------------------------


class TierPolicy:
    """
    Maps a trust score in [0, 1] to a `TrustTier` using the configured
    `tier_transition_thresholds`.

    A score at or above the ELEVATED threshold is NORMAL; below it but
    at/above RESTRICTED is ELEVATED; below that but at/above TERMINATED
    is RESTRICTED; below TERMINATED is TERMINATED. The defaults
    (NORMAL >= 0.7 > ELEVATED >= 0.4 > RESTRICTED >= 0.1 > TERMINATED)
    come from `VistaGuardSettings.tier_transition_thresholds`.

    Split from `TrustScorer` so the score->tier mapping can be reused
    for both the per-capability and session-level tiers, and so a
    deployment can subclass it without churning the scorer.
    """

    def __init__(self, settings: VistaGuardSettings) -> None:
        self._settings = settings
        thresholds = dict(getattr(settings, "tier_transition_thresholds", {}) or {})
        self._elevated = float(thresholds.get("ELEVATED", 0.7))
        self._restricted = float(thresholds.get("RESTRICTED", 0.4))
        self._terminated = float(thresholds.get("TERMINATED", 0.1))

    def tier_for_score(self, score: float) -> TrustTier:
        """Return the tier for a given score."""
        if not (0.0 <= score <= 1.0):
            logger.warning(
                "TierPolicy.tier_for_score got out-of-range score %r; "
                "clamping into [0, 1].",
                score,
            )
            score = _clamp(score)
        if score >= self._elevated:
            return TrustTier.NORMAL
        if score >= self._restricted:
            return TrustTier.ELEVATED
        if score >= self._terminated:
            return TrustTier.RESTRICTED
        return TrustTier.TERMINATED


# -----------------------------------------------------------------
# TrustScorer
# -----------------------------------------------------------------


class TrustScorer:
    """
    Per-session trust state with Bayesian per-capability credit
    assignment.

    The richer model activates as soon as gates record events; an idle
    session (no events) reports `initial_trust` and the NORMAL tier, so
    the flag-off / benign behavior is identical to Phase 0.
    """

    def __init__(
        self,
        settings: VistaGuardSettings,
        policy: TierPolicy | None = None,
    ) -> None:
        self._settings = settings
        self._policy = policy if policy is not None else TierPolicy(settings)
        self._lock = RLock()

        self._initial = _clamp(float(getattr(settings, "initial_trust", 1.0)))
        self._prior = float(getattr(settings, "trust_prior_strength", _DEFAULT_PRIOR_STRENGTH))
        self._success_weight = float(
            getattr(settings, "trust_success_weight", _DEFAULT_SUCCESS_WEIGHT)
        )
        self._failure_scale = float(
            getattr(settings, "trust_failure_scale", _DEFAULT_FAILURE_SCALE)
        )
        self._high_stakes_mult = float(
            getattr(settings, "trust_high_stakes_multiplier", _DEFAULT_HIGH_STAKES_MULT)
        )
        self._sticky_enabled = bool(getattr(settings, "sticky_high_stakes", True))

        self._caps: dict[str, _CapabilityPosterior] = {}
        self._history: list[_ViolationRecord] = []
        self._sticky_denied: list[str] = []

        # Incident-playbook state layered on top of the raw scores. A
        # SEV2 ratchets `_floor_tier` (a session-wide floor the score
        # cannot climb above) and sets `_reauth_required`; a SEV1 pins
        # `_terminated`, which forces every tier to TERMINATED.
        self._floor_tier: TrustTier | None = None
        self._terminated = False
        self._reauth_required = False

        # Domain-contract coverage, accumulated by the gates' slow-tier
        # contract checks and surfaced via the state API.
        self._contract_checked = 0
        self._contract_covered = 0
        self._contract_violations = 0

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    def _posterior_for(self, capability_kind: str) -> _CapabilityPosterior:
        """Return (creating from the prior if needed) the posterior for
        a capability kind."""
        post = self._caps.get(capability_kind)
        if post is None:
            # A fresh capability inherits the global prior, split by the
            # initial trust: initial_trust=1.0 => beta0=0 => mean 1.0.
            post = _CapabilityPosterior(
                alpha=self._prior * self._initial,
                beta=self._prior * (1.0 - self._initial),
            )
            self._caps[capability_kind] = post
        return post

    def _pooled_mean(self) -> float:
        """Posterior mean pooled across every capability (the single
        session-wide score, for the Phase-0 `current_score` surface)."""
        if not self._caps:
            return self._initial
        total_alpha = sum(p.alpha for p in self._caps.values())
        total_beta = sum(p.beta for p in self._caps.values())
        total = total_alpha + total_beta
        if total <= 0:
            return self._initial
        return _clamp(total_alpha / total)

    def _tier_for_posterior(self, post: _CapabilityPosterior) -> TrustTier:
        """The tier for one capability: its score-derived tier, never
        better than its sticky floor."""
        tier = self._policy.tier_for_score(post.mean)
        if post.floor is not None:
            tier = _worse(tier, post.floor)
        return tier

    def _apply_session_floor(self, tier: TrustTier) -> TrustTier:
        """Clamp a capability/score tier by the session-wide incident
        state: TERMINATED if the session was terminated (SEV1), else
        never better than the escalation floor (SEV2)."""
        if self._terminated:
            return TrustTier.TERMINATED
        if self._floor_tier is not None:
            return _worse(tier, self._floor_tier)
        return tier

    # -----------------------------------------------------------------
    # Read-only state
    # -----------------------------------------------------------------

    @property
    def current_score(self) -> float:
        """The pooled trust score across all capabilities, in [0, 1]."""
        with self._lock:
            return self._pooled_mean()

    @property
    def policy(self) -> TierPolicy:
        """The `TierPolicy` backing the score->tier mapping."""
        return self._policy

    @property
    def history(self) -> Sequence[_ViolationRecord]:
        """Read-only view of recorded violations."""
        with self._lock:
            return tuple(self._history)

    @property
    def last_violation(self) -> _ViolationRecord | None:
        """The most recent violation, or None if none recorded."""
        with self._lock:
            return self._history[-1] if self._history else None

    @property
    def sticky_denied(self) -> tuple[str, ...]:
        """Capability keys marked sticky-denied this session, in order."""
        with self._lock:
            return tuple(self._sticky_denied)

    @property
    def terminated(self) -> bool:
        """True once a SEV1 incident has terminated the session."""
        with self._lock:
            return self._terminated

    @property
    def reauth_required(self) -> bool:
        """True once a SEV2 incident has forced re-authentication."""
        with self._lock:
            return self._reauth_required

    def current_tier(self) -> TrustTier:
        """
        The session tier: the worst tier across all capabilities, then
        clamped by the incident state (SEV2 floor / SEV1 termination).
        With no events this is `tier_for_score(initial_trust)` -- NORMAL
        by default.
        """
        with self._lock:
            if self._terminated:
                return TrustTier.TERMINATED
            if not self._caps:
                base = self._policy.tier_for_score(self._initial)
            else:
                base = TrustTier.NORMAL
                for post in self._caps.values():
                    base = _worse(base, self._tier_for_posterior(post))
            return self._apply_session_floor(base)

    def current_tier_for(self, capability_kind: str) -> TrustTier:
        """
        The tier for a single capability kind (e.g. ``"G2"``).

        Derived from that capability's own posterior mean, never better
        than its sticky floor, then clamped by the session incident
        state. A capability with no recorded events reports
        `tier_for_score(initial_trust)`.
        """
        with self._lock:
            post = self._caps.get(capability_kind)
            if post is None:
                base = self._policy.tier_for_score(self._initial)
            else:
                base = self._tier_for_posterior(post)
            return self._apply_session_floor(base)

    def capability_scores(self) -> dict[str, float]:
        """Per-capability posterior means, keyed by capability kind."""
        with self._lock:
            return {kind: post.mean for kind, post in self._caps.items()}

    # -----------------------------------------------------------------
    # Signal recording
    # -----------------------------------------------------------------

    def record_clean_call(self, capability_kind: str | None = None) -> None:
        """
        Record a clean (non-violating) operation for a capability.

        Adds success mass to that capability's posterior, nudging its
        tier back toward NORMAL -- unless a sticky floor holds it down.
        No-op when the master flag is off.
        """
        if not self._settings.enabled:
            return
        with self._lock:
            post = self._posterior_for(capability_kind or "session")
            post.alpha += self._success_weight
            post.clean_calls += 1

    def record_violation(
        self,
        severity: int,
        *,
        capability_kind: str | None = None,
        high_stakes: bool | None = None,
    ) -> None:
        """
        Record a security violation at the given severity against a
        capability.

        `severity` is in {1, 2, 3} (SEV1/SEV2/SEV3). Higher severity
        adds more failure mass to the capability's posterior. A
        high-stakes violation adds extra mass and -- when
        ``sticky_high_stakes`` is enabled -- ratchets the capability's
        floor tier so clean calls can never restore it within the
        session (the oscillation-attack mitigation).

        `high_stakes` defaults to True for SEV1 and False otherwise.

        No-op when the master flag is off. Raises `ValueError` for an
        unrecognized severity (a programming error in the caller).
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

        if high_stakes is None:
            high_stakes = severity == 1

        with self._lock:
            kind = capability_kind or "session"
            post = self._posterior_for(kind)
            before = self._pooled_mean()

            failure = _VIOLATION_MAGNITUDE_BY_SEVERITY[severity] * self._failure_scale
            if high_stakes:
                failure *= self._high_stakes_mult
            post.beta += failure
            post.violations += 1

            if high_stakes and self._sticky_enabled:
                # The breached capability cannot recover within the
                # session: pin its floor to whatever tier it just
                # dropped to.
                post.sticky = True
                dropped_to = self._policy.tier_for_score(post.mean)
                post.floor = (
                    dropped_to if post.floor is None else _worse(post.floor, dropped_to)
                )

            after = self._pooled_mean()
            self._history.append(
                _ViolationRecord(
                    severity=severity,
                    capability_kind=capability_kind,
                    high_stakes=high_stakes,
                    score_before=before,
                    score_after=after,
                )
            )
            logger.info(
                "TrustScorer: SEV%d violation (capability_kind=%r, "
                "high_stakes=%s); pooled %.3f -> %.3f, %s tier now %s",
                severity,
                capability_kind,
                high_stakes,
                before,
                after,
                kind,
                self._tier_for_posterior(post).value,
            )

    def record_contract_check(
        self, checked: int, covered: int, violations: int
    ) -> None:
        """Fold one slow-tier contract check's coverage counts into the
        session totals (claims checked, claims bounded by >=1 contract,
        and violations found)."""
        if not self._settings.enabled:
            return
        with self._lock:
            self._contract_checked += int(checked)
            self._contract_covered += int(covered)
            self._contract_violations += int(violations)

    def contract_coverage(self) -> dict:
        """Domain-contract coverage for the session (for the state API)."""
        with self._lock:
            checked = self._contract_checked
            covered = self._contract_covered
            return {
                "claims_checked": checked,
                "claims_covered": covered,
                "coverage": (covered / checked) if checked else 0.0,
                "violations": self._contract_violations,
            }

    def mark_sticky_denied(self, key: str) -> None:
        """
        Record that a capability `key` (e.g. ``"g5:above_ceiling_resource"``)
        has been sticky-denied this session.

        This is a *registry* of denied capability keys consulted by
        gates; it deliberately does not move the trust score or tier
        (a fast-tier deny carries its own incident, which is what feeds
        the Bayesian posterior). No-op when the master flag is off.
        Keys are de-duplicated, preserving first-seen order.
        """
        if not self._settings.enabled:
            return
        with self._lock:
            if key not in self._sticky_denied:
                self._sticky_denied.append(key)

    def notify_incident(self, *, level: int, gate: str) -> None:
        """
        Receive an incident notification from `IncidentManager` and run
        the severity playbook.

        The incident's `gate` is used as the capability kind, so a SEV on
        G3 degrades G3's posterior without touching G2's. On top of that
        per-capability scoring, the playbook takes a discrete action:

          * SEV1 -> terminate the session (tier pinned TERMINATED; the
            G1 hook / agent loop then refuses any further interaction);
          * SEV2 -> elevate the session tier and force re-authentication;
          * SEV3 -> scoring only, no tier transition (logged upstream by
            `IncidentManager`, "logs without behavioral change").

        No-op when the master flag is off.
        """
        if not self._settings.enabled:
            logger.debug(
                "TrustScorer.notify_incident ignored (enabled=False): "
                "level=%d gate=%s",
                level,
                gate,
            )
            return
        self.record_violation(level, capability_kind=gate, high_stakes=level == 1)
        if level == 1:
            self.terminate(reason=f"SEV1 from {gate}")
        elif level == 2:
            self.escalate_to(TrustTier.ELEVATED, require_reauth=True)

    # -----------------------------------------------------------------
    # Incident-playbook actions (driven by `notify_incident`)
    # -----------------------------------------------------------------

    def terminate(self, reason: str = "") -> TrustTier:
        """
        Hard-stop the session (SEV1). The tier is pinned TERMINATED for
        the remainder of the session; re-authentication does not revive
        a terminated session.
        """
        with self._lock:
            self._terminated = True
            self._floor_tier = TrustTier.TERMINATED
            logger.warning("TrustScorer: session TERMINATED (%s)", reason)
            return TrustTier.TERMINATED

    def escalate_to(
        self, tier: TrustTier, *, require_reauth: bool = False
    ) -> TrustTier:
        """
        Ratchet the session floor to at least `tier` (SEV2). The floor
        never relaxes; optionally flags that the session must
        re-authenticate before high-privilege actions resume.
        """
        with self._lock:
            self._floor_tier = (
                tier if self._floor_tier is None else _worse(self._floor_tier, tier)
            )
            if require_reauth:
                self._reauth_required = True
            logger.info(
                "TrustScorer: escalated to %s floor (require_reauth=%s)",
                self._floor_tier.value,
                require_reauth,
            )
            return self.current_tier()

    def require_reauth(self) -> None:
        """Flag that the session must re-authenticate."""
        with self._lock:
            self._reauth_required = True

    def clear_reauth(self) -> None:
        """Clear the re-auth-required flag (e.g. after a re-auth flow)."""
        with self._lock:
            self._reauth_required = False

    def reauthenticate(self) -> tuple[str, ...]:
        """
        Clear sticky high-stakes lock-in after an explicit user
        re-authentication

        Sticky high-stakes capabilities (G5-above-ceiling,
        G6-destructive, G3-CUI, ...) do not auto-recover within a
        session: clean calls rebuild the posterior but the sticky floor
        holds the tier down, so a probe-then-strike oscillation attacker
        cannot wash out a breach. The *only* thing that lifts the lock is
        an explicit human re-auth, which is what this method models.

        Re-auth restores each sticky-locked capability to its prior
        (clearing the floor and the sticky flag) and empties the
        sticky-denied registry, so the previously locked capabilities
        become usable again. Non-sticky capabilities -- which already
        auto-recover via clean calls -- and the violation history are
        left untouched, so the audit trail of what happened this session
        survives the re-auth.

        Re-auth also clears the SEV2 escalation floor and the
        re-auth-required flag. It does *not* revive a SEV1-terminated
        session: termination is final, and re-auth on a terminated
        session is a no-op.

        Returns the capability kinds that were unlocked, for logging by
        the (separate) re-auth API endpoint. No-op (returns ``()``) when
        the master flag is off or the session is terminated.
        """
        if not self._settings.enabled:
            return ()
        with self._lock:
            if self._terminated:
                return ()
            unlocked: list[str] = []
            for kind, post in self._caps.items():
                if post.sticky or post.floor is not None:
                    post.alpha = self._prior * self._initial
                    post.beta = self._prior * (1.0 - self._initial)
                    post.floor = None
                    post.sticky = False
                    unlocked.append(kind)
            cleared_keys = bool(self._sticky_denied)
            self._sticky_denied.clear()
            self._floor_tier = None
            self._reauth_required = False
            if unlocked or cleared_keys:
                logger.info(
                    "TrustScorer: re-authentication cleared sticky lock-in "
                    "(capabilities=%s, sticky_denied_cleared=%s)",
                    unlocked,
                    cleared_keys,
                )
            return tuple(unlocked)

    def reset(self) -> None:
        """Reset all posteriors, the sticky-denied registry, the
        incident-playbook state, and the violation history to a fresh
        session."""
        with self._lock:
            self._caps.clear()
            self._history.clear()
            self._sticky_denied.clear()
            self._floor_tier = None
            self._terminated = False
            self._reauth_required = False
            self._contract_checked = 0
            self._contract_covered = 0
            self._contract_violations = 0

    # -----------------------------------------------------------------
    # Introspection
    # -----------------------------------------------------------------

    def snapshot(self) -> dict:
        """A JSON-serializable view of the trust state, for the state
        API endpoint and debugging."""
        with self._lock:
            return {
                "score": self._pooled_mean(),
                "tier": self.current_tier().value,
                "terminated": self._terminated,
                "reauth_required": self._reauth_required,
                "violations": len(self._history),
                "sticky_denied": list(self._sticky_denied),
                "contract_coverage": self.contract_coverage(),
                "capabilities": {
                    kind: {
                        "score": post.mean,
                        "tier": self._tier_for_posterior(post).value,
                        "clean_calls": post.clean_calls,
                        "violations": post.violations,
                        "sticky": post.sticky,
                        "floor": post.floor.value if post.floor else None,
                    }
                    for kind, post in self._caps.items()
                },
            }


__all__ = ["TierPolicy", "TrustScorer"]
