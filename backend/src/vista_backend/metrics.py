"""
VISTA metrics core (evaluation plan M1).

One metrics module, many thin probes: all timing/counting funnels through a
single `MetricsRecorder`; probes at hook points (tool-call dispatcher, agent
run loop, gates, RAG/HPC stages) are 1-5 lines each. Probes emit raw JSONL
events sharing correlation ids (`run_id`, `session_id`); statistics are
computed offline by `scripts/metrics_report.py`.

Design contract (mirrors `PalisadeSidecar`): **off by default, zero
hot-path cost when off**. With `VISTA_BACKEND_METRICS__LEVEL=off` every
probe short-circuits before building any payload and the agent behavior is
byte-identical to an uninstrumented build.

Flag taxonomy (evaluation plan §2a) — one ordered level knob plus tri-state
per-probe overrides, all under the `VISTA_BACKEND_METRICS__*` env prefix via
the parent `Settings`' `env_nested_delimiter="__"`:

==========  ============================================================
``off``     nothing (default)
``prod``    coarse events: tool calls, agent runs, gate decisions;
            sampled (default 0.1); no argument payloads; safe to leave on
``perf``    + stage timing, sampling 1.0, args sizes, skill attribution,
            per-gate duration_ms (the paper experiments run here)
``trace``   + payload capture; incident debugging only, never left on
==========  ============================================================

Events are sampled per *run* (deterministic hash of `run_id`), so a sampled
run keeps all of its events and offline joins stay intact across processes.

`stop_reason` values emitted by the M5 `agent_run` probe are runtime-level
(`completed | budget_exhausted | guard_denied | terminated | error |
user_abort`); E12b maps `completed` to campaign-level `threshold_reached`
offline via per-campaign success predicates.
"""

from __future__ import annotations

import functools
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

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

MetricsLevel = Literal["off", "prod", "perf", "trace"]
TriState = Literal["inherit", "on", "off"]

_LEVEL_ORDER: dict[str, int] = {"off": 0, "prod": 1, "perf": 2, "trace": 3}
_LEVEL_SAMPLE_RATE: dict[str, float] = {
    "off": 0.0,
    "prod": 0.1,
    "perf": 1.0,
    "trace": 1.0,
}

# Correlation ids stamped on every event. Set per run by `run_context()` in
# `ProjectAgent.run_stream`; tool/gate probes anywhere in the run's task tree
# inherit them, so client- and server-side events join offline.
run_id_var: ContextVar[str | None] = ContextVar("vista_metrics_run_id", default=None)
session_id_var: ContextVar[str | None] = ContextVar(
    "vista_metrics_session_id", default=None
)
project_id_var: ContextVar[str | None] = ContextVar(
    "vista_metrics_project_id", default=None
)


class MetricsSettings(BaseModel):
    """
    Metrics configuration, read from env as `VISTA_BACKEND_METRICS__<FIELD>`
    (and mirrored as `VISTA_MCP_METRICS__<FIELD>` in the MCP server process).
    """

    level: MetricsLevel = "off"
    """
    The ordered level knob — the only flag touched in normal operation.
    `off` is a byte-identical no-op and the master kill: granular overrides
    below have no effect at `off`.
    """

    tool_timing: TriState = "inherit"
    """Per-tool-call events from the backend's hook-owned agent path (M2).
    Minimum level `prod`; argument sizes and skill attribution only at
    `perf`."""

    stage_timing: TriState = "inherit"
    """Within-tool stage events: RAG embed/dense/BM25/fusion, S3M/IRI
    round-trips, sandbox exec (M3). Minimum level `perf`."""

    gate_timing: TriState = "inherit"
    """Per-gate `duration_ms` on PALISADE `gate_decision` provenance
    events (M4). Minimum level `perf`."""

    agent_run: TriState = "inherit"
    """One `agent_run` summary event per run: stop_reason, iterations,
    tokens, human interventions, skills loaded (M5). Minimum level `prod`."""

    skill_usage: TriState = "inherit"
    """Skill attribution on tool events + `skills_loaded` detail (M11).
    Minimum level `perf`. Typical production use for E13/E14 adoption data:
    `LEVEL=prod` + `SKILL_USAGE=on` + `SAMPLE_RATE=1.0`."""

    sample_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    """Per-run sampling rate. Default inherits from the level: 0.1 at
    `prod`, 1.0 at `perf`/`trace`."""

    log_path: Path | None = None
    """JSONL destination. Defaults to `{data_dir}/metrics/metrics.jsonl`;
    in production point this at the ops volume."""


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


@dataclass
class MetricEvent:
    """One JSONL metric event (evaluation plan §5 schema)."""

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
        # Correlation ids default from the ambient run context.
        if self.run_id is None:
            self.run_id = run_id_var.get()
        if self.session_id is None:
            self.session_id = session_id_var.get()
        if self.project_id is None:
            self.project_id = project_id_var.get()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ts": self.ts,
            "event_type": self.event_type,
            "status": self.status,
        }
        for key in ("session_id", "run_id", "project_id", "tool_name", "duration_ms"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.payload:
            out["payload"] = self.payload
        return out


class _Timer:
    __slots__ = ("_started", "duration_ms")

    def __init__(self) -> None:
        self._started = time.monotonic()
        self.duration_ms: float | None = None

    def stop(self) -> float:
        self.duration_ms = (time.monotonic() - self._started) * 1000.0
        return self.duration_ms


@contextmanager
def timer() -> Iterator[_Timer]:
    """Measure a block on `time.monotonic()`; read `t.duration_ms` after."""
    t = _Timer()
    try:
        yield t
    finally:
        t.stop()


@contextmanager
def run_context(
    session_id: str | None, project_id: str | None, run_id: str | None = None
) -> Iterator[str]:
    """
    Establish the correlation ids for one agent run. Yields the `run_id`
    (generated when not supplied). Every `MetricEvent` created inside —
    including in tasks spawned within the run — carries these ids.
    """
    rid = run_id or uuid.uuid4().hex
    tokens = (
        run_id_var.set(rid),
        session_id_var.set(session_id),
        project_id_var.set(project_id),
    )
    try:
        yield rid
    finally:
        run_id_var.reset(tokens[0])
        session_id_var.reset(tokens[1])
        project_id_var.reset(tokens[2])


def needs(min_level: MetricsLevel, override: str | None = None):
    """
    Declare a probe's minimum level (and optional tri-state override field)
    so level semantics live in one place. The wrapped recorder method
    short-circuits to None when inactive — same contract as
    `PalisadeSidecar` with the master flag off.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(self: "MetricsRecorder", *args: Any, **kwargs: Any):
            if not self.active(min_level, override):
                return None
            return fn(self, *args, **kwargs)

        return wrapper

    return decorator


class MetricsRecorder:
    """
    The single funnel for all metric events. Construct once per process
    (`get_recorder()`); tests construct directly with an explicit
    `MetricsSettings` and log path.
    """

    def __init__(
        self, settings: MetricsSettings, *, default_log_path: Path | None = None
    ) -> None:
        self._settings = settings
        self._level = _LEVEL_ORDER[settings.level]
        self._sample_rate = (
            settings.sample_rate
            if settings.sample_rate is not None
            else _LEVEL_SAMPLE_RATE[settings.level]
        )
        self._log_path: Path | None = settings.log_path or default_log_path
        self._write_failed = False

    # -----------------------------------------------------------------
    # Gating
    # -----------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """False at `level=off` — the master kill, overrides included."""
        return self._level > 0

    @property
    def detailed(self) -> bool:
        """True at `perf`+: probes may attach argument sizes, skill
        attribution, and other profiling-grade payload fields."""
        return self._level >= _LEVEL_ORDER["perf"]

    def active(self, min_level: MetricsLevel, override: str | None = None) -> bool:
        """
        Is a probe with the given minimum level (and optional override
        field) currently live? `off` wins over everything; an `on`/`off`
        override beats the level comparison otherwise.
        """
        if self._level == 0:
            return False
        if override is not None:
            tri: TriState = getattr(self._settings, override)
            if tri != "inherit":
                return tri == "on"
        return self._level >= _LEVEL_ORDER[min_level]

    def sampled(self, key: str | None = None) -> bool:
        """
        Per-run sampling decision. Deterministic in `key` (the run id), so
        every process observing the same run — backend and MCP server —
        keeps or drops it identically and offline joins stay whole. Falls
        back to per-event random sampling when no run id is in scope.
        """
        if self._sample_rate >= 1.0:
            return True
        if self._sample_rate <= 0.0:
            return False
        key = key if key is not None else run_id_var.get()
        if key is None:
            return random.random() < self._sample_rate
        digest = hashlib.md5(key.encode(), usedforsecurity=False).digest()
        return int.from_bytes(digest[:8], "big") / 2**64 < self._sample_rate

    # -----------------------------------------------------------------
    # Probes
    # -----------------------------------------------------------------

    @needs("prod", "tool_timing")
    def tool_call(
        self,
        *,
        tool_name: str,
        duration_ms: float,
        status: str = "ok",
        side: str = "client",
        payload: dict[str, Any] | None = None,
    ) -> MetricEvent | None:
        """M2/M3: one event per MCP tool call, observed client- or
        server-side (`tool_call.client` / `tool_call.server`)."""
        return self._emit(
            MetricEvent(
                event_type=f"tool_call.{side}",
                tool_name=tool_name,
                duration_ms=duration_ms,
                status=status,
                payload=payload or {},
            )
        )

    @needs("perf", "stage_timing")
    def stage(
        self,
        *,
        stage: str,
        duration_ms: float,
        status: str = "ok",
        tool_name: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> MetricEvent | None:
        """M3: within-tool stage timing (RAG embed/dense/BM25/fusion,
        S3M/IRI round-trips, sandbox exec)."""
        return self._emit(
            MetricEvent(
                event_type=f"stage.{stage}",
                tool_name=tool_name,
                duration_ms=duration_ms,
                status=status,
                payload=payload or {},
            )
        )

    @needs("perf", "gate_timing")
    def gate(
        self,
        *,
        gate: str,
        tier: str,
        duration_ms: float,
        allow: bool,
        payload: dict[str, Any] | None = None,
    ) -> MetricEvent | None:
        """M4: per-gate fast/slow tier timing from the `Gate` base-class
        dispatch (`gate.G2.fast`, `gate.G5.slow`, ...). Powers E4."""
        return self._emit(
            MetricEvent(
                event_type=f"gate.{gate}.{tier}",
                duration_ms=duration_ms,
                status="ok" if allow else "deny",
                payload=payload or {},
            )
        )

    @needs("prod", "agent_run")
    def agent_run(
        self,
        *,
        duration_ms: float,
        stop_reason: str,
        usage: Any = None,
        tool_calls: int = 0,
        human_interventions: int = 0,
        skills_loaded: list[str] | None = None,
    ) -> MetricEvent | None:
        """M5: one summary event per agent run. `usage` is a PydanticAI
        `RunUsage` (duck-typed to keep this module dependency-light)."""
        tokens: dict[str, Any] | None = None
        if usage is not None:
            tokens = {
                key: getattr(usage, attr, None)
                for key, attr in (
                    ("requests", "requests"),
                    ("tool_calls", "tool_calls"),
                    ("input", "input_tokens"),
                    ("output", "output_tokens"),
                    ("cache_read", "cache_read_tokens"),
                    ("cache_write", "cache_write_tokens"),
                )
            }
        return self._emit(
            MetricEvent(
                event_type="agent_run",
                duration_ms=duration_ms,
                status="ok" if stop_reason == "completed" else stop_reason,
                payload={
                    "stop_reason": stop_reason,
                    "iterations": getattr(usage, "requests", None),
                    "tokens": tokens,
                    "tool_calls": tool_calls,
                    "human_interventions": human_interventions,
                    "skills_loaded": skills_loaded or [],
                },
            )
        )

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    def _emit(self, event: MetricEvent) -> MetricEvent | None:
        if not self.sampled(event.run_id):
            return None
        self._write(event)
        return event

    def _write(self, event: MetricEvent) -> None:
        record = event.to_dict()
        if self._log_path is None or self._write_failed:
            logger.info(
                "metrics %s", event.event_type, extra={"vista_metric_event": record}
            )
            return
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, sort_keys=True, default=str))
                fh.write("\n")
                fh.flush()
        except OSError as exc:
            # Metrics must never break the run: log loudly once, then
            # degrade to the module logger for the rest of the process.
            self._write_failed = True
            logger.error(
                "metrics: cannot write %s (%s: %s); degrading to the module "
                "logger for the rest of this process.",
                self._log_path,
                type(exc).__name__,
                exc,
            )


_recorder: MetricsRecorder | None = None


def get_recorder() -> MetricsRecorder:
    """The per-process recorder, built lazily from `Settings` (the lazy
    import breaks the config<->metrics cycle: `config.py` imports
    `MetricsSettings` from here at module load)."""
    global _recorder
    if _recorder is None:
        from .config import settings

        _recorder = MetricsRecorder(
            settings.metrics,
            default_log_path=settings.data_dir / "metrics" / "metrics.jsonl",
        )
    return _recorder
