"""
Incident manager for VISTAGuard.

This module ships the Phase-0 incident classifier: a thin wrapper
around Python's `logging` that emits structured records at
SEV1/SEV2/SEV3 severity. Phase-5 will fill in the playbook
actions described in main proposal §2.3 (SEV1 terminates the
session, SEV2 elevates the trust tier and forces re-authentication,
SEV3 logs only). Phase 0 establishes the call site and the API
surface so gates written in Phases 1-4 can call
`IncidentManager.record(...)` and have the playbook integrate
automatically when Phase 5 lands.

## Severity mapping

| Level | Name | Python log level | Phase-5 playbook (not yet wired) |
|-------|------|------------------|----------------------------------|
| 1     | SEV1 | ERROR            | Terminate session, preserve provenance, page on-call |
| 2     | SEV2 | WARNING          | Elevate trust tier, force re-auth on next high-privilege call |
| 3     | SEV3 | INFO             | Log only |

The mapping puts the *most severe* incident at the *most severe*
log level so existing log-monitoring pipelines surface SEV1
events at their existing ERROR threshold.

## Wiring

`IncidentManager` accepts optional `trust_scorer` and `provenance`
dependencies in its constructor. When present, every `record(...)`
call invokes `trust_scorer.notify_incident(...)` and
`provenance.emit_incident(...)`. Both methods are no-op stubs in
Phase 0:

- `TrustScorer.notify_incident()` is added as a stub in the same
  PR as this module so the call site is real.
- `ProvenanceEmitter.emit_incident()` arrives in its own phase-0
  issue; until then, callers leave `provenance=None` and the
  emission no-ops via the explicit None-check here.

This is the same defensive-None pattern used by `Gate.check_slow`
for `quarantine_agent`: gates and the sidecar can call
`IncidentManager.record(...)` unconditionally without checking
which collaborators are wired up.

## No-op when the master flag is off

When `settings.vistaguard.enabled` is False, `record(...)` is a
no-op: no log line, no trust-scorer notification, no provenance
emission. This matches the contract that other VISTAGuard
components observe (`TrustScorer.record_violation` etc.) and means
gates can call this unconditionally.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
import logging
from typing import Any

from .capabilities import CapabilityTag
from .config import VistaGuardSettings


# Logger naming convention: this module's logger sits at
# `vista_backend.vistaguard.incidents` for consistency with the rest
# of the package (every other module uses `__name__`-derived names
# and inherits the same handler config). The phase-0 issue text
# refers to "a vistaguard.incidents logger"; we interpret that as
# "a stable, dedicated incidents logger" rather than the literal
# orphan-hierarchy path "vistaguard.incidents". Ops filters on
# `vista_backend.vistaguard.incidents` will catch every incident.
logger = logging.getLogger(__name__)


# -----------------------------------------------------------------
# Severity mapping
# -----------------------------------------------------------------


_SEVERITY_TO_LOG_LEVEL: dict[int, int] = {
    1: logging.ERROR,
    2: logging.WARNING,
    3: logging.INFO,
}

_SEVERITY_NAME: dict[int, str] = {
    1: "SEV1",
    2: "SEV2",
    3: "SEV3",
}

_VALID_LEVELS = frozenset(_SEVERITY_TO_LOG_LEVEL.keys())


# -----------------------------------------------------------------
# IncidentRecord
# -----------------------------------------------------------------


@dataclass(frozen=True)
class IncidentRecord:
    """
    A single incident, returned from `IncidentManager.record()`
    and exposed as `IncidentManager.last_incident` for tests and
    Phase-5 playbook code to inspect.

    Fields mirror the `record()` arguments plus a derived
    `severity_name` (`"SEV1"` etc.) for convenience.
    """

    level: int
    severity_name: str
    gate: str
    reason: str
    capability_tag: CapabilityTag | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


# -----------------------------------------------------------------
# IncidentManager
# -----------------------------------------------------------------


class IncidentManager:
    """
    Routes SEV1/SEV2/SEV3 gate decisions to the appropriate logger
    level, trust scorer, and provenance emitter.

    Phase 0 ships logging only; Phase 5 wires the playbook actions
    (SEV1 terminates session; SEV2 elevates tier and forces
    re-authentication) by adding logic to the dependencies that
    `IncidentManager` already calls. No call-site changes are
    needed in gates or the sidecar when that lands.

    Not thread-safe by design -- see `CapabilityRegistry`'s
    docstring for the rationale (single async task per
    `ProjectAgent.run_stream`).
    """

    def __init__(
        self,
        settings: VistaGuardSettings,
        *,
        trust_scorer: Any | None = None,
        provenance: Any | None = None,
    ) -> None:
        """
        Construct an IncidentManager.

        `trust_scorer` and `provenance` are typed `Any | None`
        because the IncidentManager is collaborator-agnostic --
        it just calls `notify_incident()` and `emit_incident()`
        on whatever is passed. In Phase 0 the trust scorer's
        `notify_incident()` is a no-op stub and `provenance` is
        often None entirely.

        Phase-5 PRs will tighten the type annotations to
        `TrustScorer | None` and `ProvenanceEmitter | None`
        respectively. The looseness here is forward-compatible:
        tightening a type is a narrowing change.
        """
        self._settings = settings
        self._trust_scorer = trust_scorer
        self._provenance = provenance
        self._last_incident: IncidentRecord | None = None

    # -----------------------------------------------------------------
    # Read-only state
    # -----------------------------------------------------------------

    @property
    def last_incident(self) -> IncidentRecord | None:
        """
        The most recent incident, or None if none have been
        recorded. Useful for tests; Phase-5 playbook code may
        consume this to drive rate-limited actions.

        A full incident history list is *not* in Phase-0 scope
        and arrives in Phase 5 when the playbook needs pattern
        analysis.
        """
        return self._last_incident

    # -----------------------------------------------------------------
    # Recording
    # -----------------------------------------------------------------

    def record(
        self,
        level: int,
        gate: str,
        reason: str,
        capability_tag: CapabilityTag | None = None,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> IncidentRecord | None:
        """
        Record a SEV1/SEV2/SEV3 incident.

        Args:
            level: 1 (SEV1, severe), 2 (SEV2, warning), or 3
                (SEV3, informational). Other values raise
                ValueError -- this is a programming error in the
                caller, not a runtime signal.
            gate: the name of the gate that produced the
                incident, e.g. ``"G2"``. By convention this is
                ``Gate.name`` from the gate instance. We accept
                a string rather than a `Gate` instance to avoid
                coupling IncidentManager to the gate ABC.
            reason: human-readable explanation. Required by
                convention; the empty string is technically
                allowed but discouraged.
            capability_tag: the `CapabilityTag` associated with
                the value that triggered the incident, if any.
            metadata: optional structured payload (e.g., the
                tool name, the user prompt prefix, the chunk
                hash). Logged as `extra=` for structured-logging
                consumers and stored on the returned
                `IncidentRecord`.

        Returns:
            The `IncidentRecord` that was emitted, or None when
            the master flag is off (no-op).

        Side effects, when master flag is on:

        1. Emits a log line at the level matching the severity.
        2. Notifies the trust scorer via `notify_incident(level,
           gate)` if one is wired (Phase 5 will use this to drive
           tier transitions).
        3. Emits a provenance record via `emit_incident(record)`
           if a provenance emitter is wired (Phase 7 will use
           this to ship to Flowcept).
        4. Updates `last_incident`.

        No-op when the master flag is off: no log line, no
        notifications, no last_incident update. Validation of
        ``level`` still happens so that bad calls are loud.
        """
        if level not in _VALID_LEVELS:
            raise ValueError(
                f"level must be one of {sorted(_VALID_LEVELS)} "
                f"(SEV1/SEV2/SEV3); got {level!r}"
            )

        if not self._settings.enabled:
            # Master flag off: validate args (above) but otherwise
            # no-op so callers can invoke this unconditionally.
            return None

        record_obj = IncidentRecord(
            level=level,
            severity_name=_SEVERITY_NAME[level],
            gate=gate,
            reason=reason,
            capability_tag=capability_tag,
            metadata=dict(metadata) if metadata is not None else {},
        )

        # 1. Log emission at the matching level. The message is
        # human-readable; structured fields go in `extra=` so
        # JSON-log adapters can consume them without parsing.
        log_level = _SEVERITY_TO_LOG_LEVEL[level]
        log_extra: dict[str, Any] = {
            "vistaguard_incident_level": level,
            "vistaguard_gate": gate,
            "vistaguard_capability_tag": (
                _capability_tag_to_dict(capability_tag)
                if capability_tag is not None
                else None
            ),
        }
        if record_obj.metadata:
            log_extra["vistaguard_metadata"] = dict(record_obj.metadata)
        logger.log(
            log_level,
            "VISTAGuard %s at %s: %s",
            record_obj.severity_name,
            gate,
            reason,
            extra=log_extra,
        )

        # 2. Trust-scorer notification (Phase-5 stub in Phase 0).
        # `notify_incident` exists as a no-op on TrustScorer;
        # Phase 5 will fill in the body to drive tier transitions.
        if self._trust_scorer is not None and hasattr(
            self._trust_scorer, "notify_incident"
        ):
            self._trust_scorer.notify_incident(level=level, gate=gate)

        # 3. Provenance emission. `ProvenanceEmitter` is a separate
        # Phase-0 issue; until it lands, callers leave
        # provenance=None and we no-op here. The hasattr check
        # makes the call resilient to phase ordering.
        if self._provenance is not None and hasattr(
            self._provenance, "emit_incident"
        ):
            self._provenance.emit_incident(record_obj)

        # 4. Cache for `last_incident`.
        self._last_incident = record_obj

        return record_obj


# -----------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------


def _capability_tag_to_dict(tag: CapabilityTag) -> dict[str, Any]:
    """
    Serialize a CapabilityTag to a plain dict for `extra=` log
    payloads. Kept module-private; callers that need to serialize
    tags for other purposes (e.g. the provenance emitter) should
    define their own serializer fit for that purpose.
    """
    return {
        "source": tag.source,
        "sensitivity": tag.sensitivity.value,
        "dual_use": tag.dual_use.value,
        "taint": tag.taint,
        "provenance_chain": list(tag.provenance_chain),
        "metadata": dict(tag.metadata),
    }