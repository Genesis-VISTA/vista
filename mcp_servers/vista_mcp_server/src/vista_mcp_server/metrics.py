"""
Metrics probes for the VISTA MCP Server (evaluation plan M3).

Process-local mirror of `backend/src/vista_backend/metrics.py`: same JSONL
event schema, same level/override semantics, and the same deterministic
per-run sampling rule, so client-side (M2) and server-side events join
offline on `run_id` and a sampled run is kept or dropped identically in
both processes. `M2 − M3` per tool call isolates network + client
overhead; `stage.*` events break tool time into within-tool stages (RAG
embed/dense/bm25/fusion/citation, HPC submit/status round-trips).

Configured via `VISTA_MCP_METRICS__<FIELD>` (mirroring
`VISTA_BACKEND_METRICS__*`). `level=off` (default) is a byte-identical
no-op: the middleware is not even registered and `stage()` takes no clock
readings. Both processes default to the same JSONL file
(`{data_dir}/metrics/metrics.jsonl`); each event line is a single
append+flush, safe for two writers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

MetricsLevel = Literal["off", "prod", "perf", "trace"]
TriState = Literal["inherit", "on", "off"]

_LEVEL_ORDER: dict[str, int] = {"off": 0, "prod": 1, "perf": 2, "trace": 3}
_LEVEL_SAMPLE_RATE: dict[str, float] = {"off": 0.0, "prod": 0.1, "perf": 1.0, "trace": 1.0}

# Correlation ids for the current MCP request, set by `MetricsMiddleware`
# from the `vista.metrics` request metadata the backend's M2 probe sends.
run_id_var: ContextVar[str | None] = ContextVar("vista_mcp_metrics_run_id", default=None)
session_id_var: ContextVar[str | None] = ContextVar("vista_mcp_metrics_session_id", default=None)


class MetricsSettings(BaseModel):
    """Read from env as `VISTA_MCP_METRICS__<FIELD>` via the nested
    delimiter on `AppSettings`. Field semantics match the backend's
    `MetricsSettings`; only the probes this process owns are configurable."""

    level: MetricsLevel = "off"
    tool_timing: TriState = "inherit"
    """Server-side per-tool-call events from the middleware. Min level `prod`."""
    stage_timing: TriState = "inherit"
    """Within-tool stage events (RAG/HPC). Min level `perf`."""
    sample_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    log_path: Path | None = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class MetricEvent:
    """One JSONL metric event — schema shared with the backend (plan §5)."""

    event_type: str
    ts: str = field(default_factory=_utc_now_iso)
    duration_ms: float | None = None
    session_id: str | None = None
    project_id: str | None = None
    run_id: str | None = None
    tool_name: str | None = None
    status: str = "ok"
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.run_id is None:
            self.run_id = run_id_var.get()
        if self.session_id is None:
            self.session_id = session_id_var.get()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ts": self.ts, "event_type": self.event_type, "status": self.status}
        for key in ("session_id", "run_id", "project_id", "tool_name", "duration_ms"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.payload:
            out["payload"] = self.payload
        return out


class MetricsRecorder:
    """The single funnel for this process's metric events."""

    def __init__(self, settings: MetricsSettings, *, default_log_path: Path | None = None) -> None:
        self._settings = settings
        self._level = _LEVEL_ORDER[settings.level]
        self._sample_rate = (
            settings.sample_rate
            if settings.sample_rate is not None
            else _LEVEL_SAMPLE_RATE[settings.level]
        )
        self._log_path: Path | None = settings.log_path or default_log_path
        self._write_failed = False

    @property
    def enabled(self) -> bool:
        return self._level > 0

    def active(self, min_level: MetricsLevel, override: str | None = None) -> bool:
        if self._level == 0:
            return False
        if override is not None:
            tri: TriState = getattr(self._settings, override)
            if tri != "inherit":
                return tri == "on"
        return self._level >= _LEVEL_ORDER[min_level]

    def sampled(self, key: str | None = None) -> bool:
        """Deterministic in the run id — must match the backend's rule
        byte-for-byte so both processes keep the same runs."""
        if self._sample_rate >= 1.0:
            return True
        if self._sample_rate <= 0.0:
            return False
        key = key if key is not None else run_id_var.get()
        if key is None:
            return random.random() < self._sample_rate
        digest = hashlib.md5(key.encode()).digest()
        return int.from_bytes(digest[:8], "big") / 2**64 < self._sample_rate

    def tool_call(
        self,
        *,
        tool_name: str,
        duration_ms: float,
        status: str = "ok",
        payload: dict[str, Any] | None = None,
    ) -> MetricEvent | None:
        """M3: one `tool_call.server` event per MCP tool call."""
        if not self.active("prod", "tool_timing"):
            return None
        return self._emit(
            MetricEvent(
                event_type="tool_call.server",
                tool_name=tool_name,
                duration_ms=duration_ms,
                status=status,
                payload=payload or {},
            )
        )

    def stage(
        self,
        *,
        stage: str,
        duration_ms: float,
        status: str = "ok",
        tool_name: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> MetricEvent | None:
        """M3: within-tool stage timing (`stage.rag.embed`, `stage.hpc.submit`, ...)."""
        if not self.active("perf", "stage_timing"):
            return None
        return self._emit(
            MetricEvent(
                event_type=f"stage.{stage}",
                tool_name=tool_name,
                duration_ms=duration_ms,
                status=status,
                payload=payload or {},
            )
        )

    def _emit(self, event: MetricEvent) -> MetricEvent | None:
        if not self.sampled(event.run_id):
            return None
        record = event.to_dict()
        if self._log_path is None or self._write_failed:
            logger.info("metrics %s", event.event_type, extra={"vista_metric_event": record})
            return event
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, sort_keys=True, default=str))
                fh.write("\n")
                fh.flush()
        except OSError as exc:
            self._write_failed = True
            logger.error(
                "metrics: cannot write %s (%s: %s); degrading to the module "
                "logger for the rest of this process.",
                self._log_path, type(exc).__name__, exc,
            )
        return event


_recorder: MetricsRecorder | None = None


def get_recorder() -> MetricsRecorder:
    """Per-process recorder, built lazily from `AppSettings` (lazy import
    breaks the config<->metrics cycle)."""
    global _recorder
    if _recorder is None:
        from .config import settings

        _recorder = MetricsRecorder(
            settings.metrics,
            default_log_path=settings.data_dir / "metrics" / "metrics.jsonl",
        )
    return _recorder


@contextmanager
def request_context(run_id: str | None, session_id: str | None) -> Iterator[None]:
    """Bind the correlation ids sent by the backend (M2) for the duration
    of one tool-call request."""
    tokens = (run_id_var.set(run_id), session_id_var.set(session_id))
    try:
        yield
    finally:
        run_id_var.reset(tokens[0])
        session_id_var.reset(tokens[1])


@contextmanager
def stage(
    name: str, tool_name: str | None = None, payload: dict[str, Any] | None = None
) -> Iterator[None]:
    """One-line stage probe: `with stage("rag.embed", tool_name="rag_search"):`.
    No clock readings when stage timing is inactive; an exception inside the
    block records `status="error"` and propagates."""
    recorder = get_recorder()
    if not recorder.active("perf", "stage_timing"):
        yield
        return
    started = time.monotonic()
    status = "ok"
    try:
        yield
    except BaseException:
        status = "error"
        raise
    finally:
        recorder.stage(
            stage=name,
            duration_ms=(time.monotonic() - started) * 1000,
            status=status,
            tool_name=tool_name,
            payload=payload,
        )


def _correlation_ids(context: "MiddlewareContext") -> dict[str, Any]:
    """
    Pull `{run_id, session_id}` from the request's `vista.metrics` metadata,
    set by the backend's M2 dispatcher. Reads the request `_meta` the same
    way `lib.user_config.get_vista_meta` does — via the FastMCP context's
    `request_context.meta` (the message object the middleware receives does
    not always carry `_meta`). Falls back to `context.message.meta`.
    """
    meta = None
    fctx = getattr(context, "fastmcp_context", None)
    rc = getattr(fctx, "request_context", None) if fctx is not None else None
    if rc is not None:
        meta = getattr(rc, "meta", None)
    if meta is None:
        meta = getattr(context.message, "meta", None)
    vista = getattr(meta, "vista", None) if meta is not None else None
    if isinstance(vista, dict) and isinstance(vista.get("metrics"), dict):
        return vista["metrics"]
    return {}


class MetricsMiddleware(Middleware):
    """
    M3: time every tool call server-side (`tool_call.server`), correlated
    with the backend's client-side events via the `vista.metrics` request
    metadata. Registered in `server.py` only when the recorder is enabled,
    so the disabled path adds no per-request hop at all.

    Tool errors that FastMCP converts to error *results* (rather than
    raising through the middleware chain) are recorded as `status="ok"`
    here; the client-side M2 probe still records them as errors.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next: CallNext,
    ) -> Any:
        recorder = get_recorder()
        if not recorder.enabled:
            return await call_next(context)
        ids = _correlation_ids(context)
        with request_context(ids.get("run_id"), ids.get("session_id")):
            started = time.monotonic()
            status = "ok"
            try:
                return await call_next(context)
            except BaseException:
                status = "error"
                raise
            finally:
                recorder.tool_call(
                    tool_name=context.message.name,
                    duration_ms=(time.monotonic() - started) * 1000,
                    status=status,
                )
