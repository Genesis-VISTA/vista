"""
Provenance emitter for VISTAGuard.

"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any
import uuid

from .capabilities import CapabilityTag
from .config import VistaGuardSettings
from .gates.base import GateContext, GateDecision
from .incidents import IncidentRecord


logger = logging.getLogger(__name__)


# -----------------------------------------------------------------
# Event-type constants
# -----------------------------------------------------------------
#
# The two event types Phase 0 emits. Exposed as module-level
# constants so consumers (and the JSONL-tail-reader tests) can
# filter without hard-coding the strings. Phase 7's Flowcept
# topic/routing-key conventions will likely reuse these.

EVENT_GATE_DECISION = "gate_decision"
EVENT_INCIDENT = "incident"


# -----------------------------------------------------------------
# ProvenanceEvent
# -----------------------------------------------------------------


@dataclass(frozen=True)
class ProvenanceEvent:
    """
    A single audit event, returned from `ProvenanceEmitter.emit_*`
    and exposed as `ProvenanceEmitter.last_event` for tests and the
    Phase-5 incident-manager rate-limiting path.

    The `payload` field is the serialized JSON-compatible dict that
    was written to the log; reading it back is the simplest way for
    tests to assert on the event schema without re-running the
    serializer.
    """

    event_type: str
    timestamp: str
    session_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)


# -----------------------------------------------------------------
# ProvenanceEmitter
# -----------------------------------------------------------------


class ProvenanceEmitter:
    """
    Serializes VISTAGuard gate decisions and incidents to a JSONL
    log file (or the module logger when no path is configured).

    Phase 0 ships the log-file path only. Phase 7 will add a
    Flowcept-broker code path; until then, constructing an emitter
    with `flowcept_enabled=True` is allowed (as long as
    `flowcept_endpoint` is set) but every emit call raises
    `NotImplementedError`. This lets Phase-7 work be a body-only
    change with no call-site churn.

    Not thread-safe by design. See `CapabilityRegistry`'s docstring
    for the rationale (one async task per `ProjectAgent.run_stream`).
    If a future deployment needs cross-task emission, wrap the
    file-write under a lock at the call site -- the Phase-0
    no-contention path is the common one.
    """

    def __init__(
        self,
        settings: VistaGuardSettings,
        *,
        session_id: str | None = None,
        log_path: str | Path | None = None,
    ) -> None:
        """
        Construct a ProvenanceEmitter.

        Args:
            settings: the VISTAGuard settings sub-model. The master
                `enabled` flag, the Flowcept toggle/endpoint, and
                the optional `provenance_log_path` are read from
                here.
            session_id: a stable identifier for the session this
                emitter audits. When None, a UUID4 hex is generated
                at construction time so every event from this
                emitter shares an id. Phase 0 does not yet wire
                session ids through `ProjectAgent`; callers should
                pass the eventual session identifier when it is
                available.
            log_path: explicit override for the JSONL destination.
                Takes precedence over `settings.provenance_log_path`.
                Useful for tests; production code typically relies
                on the settings field so all emitters in a
                deployment share a destination.

        Raises:
            ValueError: if `settings.flowcept_enabled is True` and
                `settings.flowcept_endpoint is None`. This is the
                fail-fast contract from the phase-0 work item:
                a Flowcept-enabled deployment without an endpoint
                is mis-configured at boot, and surfacing that here
                is more useful than waiting for the first gate hit.
        """
        if settings.flowcept_enabled and settings.flowcept_endpoint is None:
            raise ValueError(
                "VistaGuardSettings: flowcept_enabled=True requires "
                "flowcept_endpoint to be set. Configure "
                "VISTA_BACKEND_VISTAGUARD__FLOWCEPT_ENDPOINT or leave "
                "VISTA_BACKEND_VISTAGUARD__FLOWCEPT_ENABLED at its "
                "default (False)."
            )

        self._settings = settings
        self._session_id = (
            session_id if session_id is not None else uuid.uuid4().hex
        )

        # Resolve the log destination. Explicit constructor argument
        # wins, settings second, otherwise we fall back to the module
        # logger.
        resolved_path = log_path if log_path is not None else settings.provenance_log_path
        self._log_path: Path | None = (
            Path(resolved_path) if resolved_path is not None else None
        )

        self._last_event: ProvenanceEvent | None = None

    # -----------------------------------------------------------------
    # Read-only state
    # -----------------------------------------------------------------

    @property
    def session_id(self) -> str:
        """The session identifier stamped on every event."""
        return self._session_id

    @property
    def log_path(self) -> Path | None:
        """
        The resolved JSONL destination, or None when events go
        through the module logger.
        """
        return self._log_path

    @property
    def last_event(self) -> ProvenanceEvent | None:
        """
        The most recent event, or None if none have been emitted.
        Useful for tests and for Phase-5 rate-limiting logic in the
        incident manager. Not a full history -- the JSONL file (or
        Phase-7 Flowcept) is the authoritative record.
        """
        return self._last_event

    # -----------------------------------------------------------------
    # Emit methods
    # -----------------------------------------------------------------

    def emit_gate_decision(
        self,
        gate: str,
        decision: GateDecision,
        context: GateContext | None = None,
    ) -> ProvenanceEvent | None:
        """
        Record a gate's `GateDecision` for audit.

        Args:
            gate: short identifier of the gate (e.g., ``"G2"``). By
                convention this is the `Gate.name` attribute of the
                emitting gate.
            decision: the `GateDecision` produced. The summary
                captures `allow`, `reason`, `incident_level`, and
                whether `rewritten_args` was set; the
                `capability_tag` field is serialized in full when
                present.
            context: optional `GateContext`. Phase 0 does not read
                fields off the context yet -- the schema does not
                require it -- but later phases may emit the current
                trust tier alongside the decision so audit reviewers
                can correlate without joining against a separate
                scorer log. Accepting the context now keeps that
                forward-compatible.

        Returns:
            The `ProvenanceEvent` that was emitted, or None when
            the master flag is off (no-op).
        """
        if not self._settings.enabled:
            return None

        payload: dict[str, Any] = {
            "gate": gate,
            "decision": _summarize_decision(decision),
        }
        if decision.capability_tag is not None:
            payload["capability_tag"] = _capability_tag_to_dict(
                decision.capability_tag
            )
        # Phase-0 context handling: the GateContext fields that are
        # safe to serialize (none of the registry / scorer / agent
        # references are JSON-compatible) come from the metadata
        # dict, which is documented as gate-set per-call annotations.
        if context is not None and context.metadata:
            payload["context_metadata"] = dict(context.metadata)

        return self._emit(EVENT_GATE_DECISION, payload)

    def emit_incident(self, record: IncidentRecord) -> ProvenanceEvent | None:
        """
        Record an `IncidentRecord` produced by `IncidentManager`.

        The signature accepts the same `IncidentRecord` instance
        `IncidentManager.record()` returns; `IncidentManager` calls
        this as `provenance.emit_incident(record_obj)` once the
        emitter is wired into its constructor.

        Returns:
            The `ProvenanceEvent` that was emitted, or None when
            the master flag is off (no-op).
        """
        if not self._settings.enabled:
            return None

        payload: dict[str, Any] = {
            "level": record.level,
            "severity_name": record.severity_name,
            "gate": record.gate,
            "reason": record.reason,
        }
        if record.capability_tag is not None:
            payload["capability_tag"] = _capability_tag_to_dict(
                record.capability_tag
            )
        if record.metadata:
            payload["metadata"] = dict(record.metadata)

        return self._emit(EVENT_INCIDENT, payload)

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    def _emit(self, event_type: str, payload: Mapping[str, Any]) -> ProvenanceEvent:
        """
        Common emit path used by both `emit_gate_decision` and
        `emit_incident`. Builds the event envelope, dispatches to
        either the Flowcept stub or the JSONL writer, caches as
        `last_event`, and returns the event.

        Assumes the master flag was already checked by the public
        caller -- this is internal-only and not exposed.
        """
        event = ProvenanceEvent(
            event_type=event_type,
            timestamp=_utc_now_iso(),
            session_id=self._session_id,
            payload=dict(payload),
        )

        if self._settings.flowcept_enabled:
            # Phase-0 stub: the broker integration lands in Phase 7.
            # NotImplementedError is preferable to silent dropping
            # because a deployment that flips `flowcept_enabled=True`
            # before Phase 7 should learn immediately that the broker
            # is not wired yet rather than accumulating a backlog of
            # events on the filesystem.
            raise NotImplementedError(
                "ProvenanceEmitter: Flowcept broker emission is not "
                "implemented in Phase 0. Set "
                "VISTA_BACKEND_VISTAGUARD__FLOWCEPT_ENABLED=false to "
                "use the JSONL log path until Phase 7 lands."
            )

        self._write_jsonl(event)
        self._last_event = event
        return event

    def _write_jsonl(self, event: ProvenanceEvent) -> None:
        """
        Serialize `event` and write it.

        - If a `log_path` is configured, append one JSON line to
          that file (open/append/flush/close per call -- see the
          module docstring for the durability rationale).
        - Otherwise, log at INFO via the module logger with the
          serialized payload in the `extra=` slot so JSON-log
          adapters can consume the structured fields without
          parsing the message.
        """
        record_dict = {
            "event_type": event.event_type,
            "timestamp": event.timestamp,
            "session_id": event.session_id,
            **event.payload,
        }
        line = json.dumps(record_dict, sort_keys=True, default=str)

        if self._log_path is not None:
            # `parents=True` so a fresh deployment can write to
            # `logs/vistaguard/provenance.jsonl` without operators
            # creating the directory by hand. `exist_ok=True` keeps
            # this idempotent across re-entries.
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.write("\n")
                fh.flush()
            return

        logger.info(
            "VISTAGuard provenance %s session=%s",
            event.event_type,
            event.session_id,
            extra={
                "vistaguard_provenance_event": record_dict,
            },
        )


# -----------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------


def _utc_now_iso() -> str:
    """
    Return the current UTC time as an ISO 8601 string with the
    trailing 'Z' suffix consumers expect for UTC timestamps. The
    explicit `replace(tzinfo=None)` + 'Z' pattern is preferred over
    `isoformat()` of an aware datetime because Phase-7 broker
    consumers vary in how they parse the `+00:00` form.
    """
    return (
        datetime.now(tz=timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S.%f")
    )[:-3] + "Z"


def _summarize_decision(decision: GateDecision) -> dict[str, Any]:
    """
    Reduce a `GateDecision` to its JSON-safe summary fields. The
    capability tag is serialized separately by the caller because
    it shows up at the top level of the event payload, not nested
    under `decision`.
    """
    return {
        "allow": decision.allow,
        "reason": decision.reason,
        "incident_level": decision.incident_level,
        "has_rewritten_args": decision.rewritten_args is not None,
    }


def _capability_tag_to_dict(tag: CapabilityTag) -> dict[str, Any]:
    """
    Serialize a `CapabilityTag` to a plain dict for inclusion in
    the JSONL payload. This is the same shape `IncidentManager`
    uses for its `extra=` log payloads; kept duplicated rather than
    imported because the two consumers serve different sinks
    (logger `extra=` vs. JSONL file) and may diverge.
    """
    return {
        "source": tag.source,
        "sensitivity": tag.sensitivity.value,
        "dual_use": tag.dual_use.value,
        "taint": tag.taint,
        "provenance_chain": list(tag.provenance_chain),
        "metadata": dict(tag.metadata),
    }
