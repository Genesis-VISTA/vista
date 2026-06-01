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
# Broker sink (Phase 7)
# -----------------------------------------------------------------


class FlowceptSink:
    """
    Ships `ProvenanceEvent`s to a Flowcept broker.

    `flowcept` is an **optional dependency**, imported lazily on the first
    emit so that importing VISTAGuard -- and constructing a
    `ProvenanceEmitter` -- never requires it. Each provenance event is
    published as a Flowcept *task* grouped under a per-session *workflow*
    (the workflow id is the emitter's `session_id`), so an auditor can
    pull every gate decision and incident for a session as one trace.

    The actual broker call is isolated in `_publish`, which targets
    Flowcept's documented ``Flowcept.db.insert_or_update_task`` API. It is
    written defensively (attribute-probed, never assuming a private shape)
    so a Flowcept version delta is a single-method fix, and so a broker or
    API problem surfaces as an exception the `ProvenanceEmitter` catches
    and degrades from -- provenance must never crash the agent run.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        session_id: str,
        workflow_name: str = "vistaguard",
    ) -> None:
        self._endpoint = endpoint
        self._session_id = session_id
        self._workflow_name = workflow_name
        self._flowcept: Any | None = None
        self._db: Any | None = None
        self._started = False

    def _ensure_started(self) -> None:
        """Lazily import flowcept and start the controller once."""
        if self._started:
            return
        try:
            from flowcept import Flowcept  # type: ignore
        except ImportError as exc:  # optional dependency not installed
            raise ImportError(
                "VISTAGuard: flowcept_enabled=True but the 'flowcept' "
                "package is not installed. Install the optional dependency "
                "(e.g. `uv pip install flowcept` or the 'vistaguard-flowcept' "
                "extra), or set "
                "VISTA_BACKEND_VISTAGUARD__FLOWCEPT_ENABLED=false."
            ) from exc

        # Flowcept reads its MQ/DB connection from its own configuration;
        # we record the operator-supplied endpoint on the controller so a
        # deployment can route both through the same value. Controller
        # construction kwargs are kept minimal and probed defensively.
        self._flowcept = Flowcept(
            workflow_name=self._workflow_name,
            workflow_id=self._session_id,
        )
        start = getattr(self._flowcept, "start", None)
        if callable(start):
            start()
        # `Flowcept.db` is the class-level persistence/DB API.
        self._db = getattr(Flowcept, "db", None)
        self._started = True

    def emit(self, event: "ProvenanceEvent") -> None:
        self._ensure_started()
        self._publish(self._event_to_task(event))

    def _event_to_task(self, event: "ProvenanceEvent") -> dict[str, Any]:
        """Map a `ProvenanceEvent` to a Flowcept task message."""
        return {
            "task_id": f"{event.session_id}:{event.event_type}:{event.timestamp}",
            "workflow_id": event.session_id,
            "activity_id": event.event_type,
            "used": {"session_id": event.session_id, "endpoint": self._endpoint},
            "generated": dict(event.payload),
            "custom_metadata": {
                "vistaguard": True,
                "event_type": event.event_type,
                **dict(event.payload),
            },
            "started_at": event.timestamp,
            "ended_at": event.timestamp,
        }

    def _publish(self, task: dict[str, Any]) -> None:
        if self._db is None or not hasattr(self._db, "insert_or_update_task"):
            raise RuntimeError(
                "VISTAGuard: flowcept DB API unavailable "
                "(Flowcept.db.insert_or_update_task missing); "
                "cannot publish provenance task"
            )
        self._db.insert_or_update_task(task)

    def close(self) -> None:
        """Best-effort flush/stop of the Flowcept controller."""
        if self._flowcept is not None:
            stop = getattr(self._flowcept, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception as exc:  # noqa: BLE001 - teardown must not raise
                    logger.warning(
                        "VISTAGuard: error stopping Flowcept controller (%s: %s)",
                        type(exc).__name__, exc,
                    )
        self._flowcept = None
        self._db = None
        self._started = False


# -----------------------------------------------------------------
# ProvenanceEvent
# -----------------------------------------------------------------


@dataclass(frozen=True)
class ProvenanceEvent:
    """
    A single audit event, returned from `ProvenanceEmitter.emit_*`
    and exposed as `ProvenanceEmitter.last_event` for tests and the
    Phase-5 incident-manager rate-limiting path.
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
        sink: Any | None = None,
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

        # Phase-7 broker sink. When Flowcept is enabled we ship events to
        # the broker; an injected `sink` (tests, or an alternate broker)
        # takes precedence over the default `FlowceptSink`. The sink is
        # constructed eagerly but imports `flowcept` lazily, so this does
        # not require the optional dependency at construction time.
        if sink is not None:
            self._sink: Any | None = sink
        elif settings.flowcept_enabled:
            self._sink = FlowceptSink(
                settings.flowcept_endpoint,  # validated non-None above
                session_id=self._session_id,
            )
        else:
            self._sink = None
        # Once the broker sink fails (e.g. flowcept not installed), stop
        # retrying for this session and degrade to the JSONL log so the
        # agent run is never blocked on provenance.
        self._sink_failed = False

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

        if self._sink is not None and not self._sink_failed:
            try:
                self._sink.emit(event)
            except Exception as exc:  # noqa: BLE001 - provenance must not crash the run
                # First failure (e.g. flowcept missing, broker
                # unreachable): log loudly once, then degrade to the
                # durable JSONL path for the rest of the session rather
                # than spamming the log or blocking the agent.
                self._sink_failed = True
                logger.error(
                    "VISTAGuard: Flowcept emission failed (%s: %s); "
                    "falling back to the JSONL provenance log for the "
                    "rest of this session.",
                    type(exc).__name__, exc,
                )
                self._write_jsonl(event)
            self._last_event = event
            return event

        self._write_jsonl(event)
        self._last_event = event
        return event

    def close(self) -> None:
        """Flush/stop the broker sink (best-effort). Safe to call when no
        sink is configured."""
        if self._sink is not None:
            close = getattr(self._sink, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:  # noqa: BLE001 - teardown must not raise
                    logger.warning(
                        "VISTAGuard: error closing provenance sink (%s: %s)",
                        type(exc).__name__, exc,
                    )

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
