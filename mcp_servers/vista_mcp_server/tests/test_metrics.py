"""
Tests for the MCP-server metrics mirror (evaluation plan M3): the
VISTA_MCP_METRICS__* settings, the off-is-a-no-op contract, the `stage()`
probe, cross-process sampling consistency, and the tool-call middleware's
correlation-id extraction from the backend's request metadata.
"""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import vista_mcp_server.metrics as metrics_module
from vista_mcp_server.metrics import (
    MetricsMiddleware,
    MetricsRecorder,
    MetricsSettings,
    request_context,
    stage,
)


def make_recorder(tmp_path: Path, level: str = "perf", **kwargs) -> tuple[MetricsRecorder, Path]:
    log_path = tmp_path / "metrics.jsonl"
    return MetricsRecorder(MetricsSettings(level=level, **kwargs), default_log_path=log_path), log_path


def read_events(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines()]


# ---------------------------------------------------------------------------
# Settings + gating
# ---------------------------------------------------------------------------

def test_settings_env_parsing(monkeypatch):
    monkeypatch.setenv("VISTA_MCP_METRICS__LEVEL", "perf")
    monkeypatch.setenv("VISTA_MCP_METRICS__STAGE_TIMING", "off")
    from vista_mcp_server.config import AppSettings

    settings = AppSettings()
    assert settings.metrics.level == "perf"
    assert settings.metrics.stage_timing == "off"


def test_off_is_noop(tmp_path, monkeypatch):
    rec, log_path = make_recorder(tmp_path, level="off")
    monkeypatch.setattr(metrics_module, "_recorder", rec)
    assert rec.enabled is False
    assert rec.tool_call(tool_name="t", duration_ms=1.0) is None
    with stage("rag.embed"):
        pass
    assert not log_path.exists()


def test_stage_levels(tmp_path):
    rec_prod, log_prod = make_recorder(tmp_path / "prod", level="prod", sample_rate=1.0)
    assert rec_prod.stage(stage="rag.embed", duration_ms=1.0) is None  # perf-only
    assert rec_prod.tool_call(tool_name="rag_search", duration_ms=1.0) is not None
    assert [e["event_type"] for e in read_events(log_prod)] == ["tool_call.server"]


def test_sampling_rule_matches_backend(tmp_path):
    """The keep/drop rule must be byte-identical to the backend's so a
    sampled run keeps both its client- and server-side events."""
    rec, _ = make_recorder(tmp_path, level="prod", sample_rate=0.5)
    for key in ("run-a", "run-b", "run-c", "run-d"):
        digest = hashlib.md5(key.encode()).digest()
        expected = int.from_bytes(digest[:8], "big") / 2**64 < 0.5
        assert rec.sampled(key) == expected


# ---------------------------------------------------------------------------
# stage() probe
# ---------------------------------------------------------------------------

def test_stage_emits_with_correlation(tmp_path, monkeypatch):
    rec, log_path = make_recorder(tmp_path)
    monkeypatch.setattr(metrics_module, "_recorder", rec)

    with request_context("run-1", "proj-alice"):
        with stage("hpc.submit", tool_name="submit_hpc_job", payload={"cluster": "odo"}):
            pass
    (event,) = read_events(log_path)
    assert event["event_type"] == "stage.hpc.submit"
    assert event["tool_name"] == "submit_hpc_job"
    assert event["run_id"] == "run-1"
    assert event["session_id"] == "proj-alice"
    assert event["payload"] == {"cluster": "odo"}
    assert event["duration_ms"] >= 0.0


def test_stage_records_errors_and_reraises(tmp_path, monkeypatch):
    rec, log_path = make_recorder(tmp_path)
    monkeypatch.setattr(metrics_module, "_recorder", rec)

    with pytest.raises(RuntimeError):
        with stage("rag.dense"):
            raise RuntimeError("chroma down")
    (event,) = read_events(log_path)
    assert event["status"] == "error"


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

def make_context(tool: str, vista_meta: dict | None):
    """Mimic the real middleware context: the request `_meta` is reached via
    `fastmcp_context.request_context.meta` (the same source get_vista_meta
    uses), not the message object."""
    meta = SimpleNamespace(vista=vista_meta) if vista_meta is not None else None
    request_context = SimpleNamespace(meta=meta)
    fastmcp_context = SimpleNamespace(request_context=request_context)
    return SimpleNamespace(
        message=SimpleNamespace(name=tool, meta=None),
        fastmcp_context=fastmcp_context,
    )


@pytest.mark.anyio
async def test_middleware_times_and_correlates(tmp_path, monkeypatch):
    rec, log_path = make_recorder(tmp_path)
    monkeypatch.setattr(metrics_module, "_recorder", rec)

    async def call_next(context):
        return "result"

    context = make_context(
        "rag_search", {"metrics": {"run_id": "run-9", "session_id": "proj-bob"}}
    )
    assert await MetricsMiddleware().on_call_tool(context, call_next) == "result"

    (event,) = read_events(log_path)
    assert event["event_type"] == "tool_call.server"
    assert event["tool_name"] == "rag_search"
    assert event["run_id"] == "run-9"
    assert event["session_id"] == "proj-bob"
    assert event["status"] == "ok"


@pytest.mark.anyio
async def test_middleware_without_metadata_and_on_error(tmp_path, monkeypatch):
    """Calls without the backend's metrics metadata (other MCP clients)
    are still timed; tool exceptions record status=error and propagate."""
    rec, log_path = make_recorder(tmp_path)
    monkeypatch.setattr(metrics_module, "_recorder", rec)

    async def boom(context):
        raise ValueError("bad args")

    with pytest.raises(ValueError):
        await MetricsMiddleware().on_call_tool(make_context("view", None), boom)

    (event,) = read_events(log_path)
    assert event["event_type"] == "tool_call.server"
    assert event["status"] == "error"
    assert "run_id" not in event


@pytest.mark.anyio
async def test_middleware_noop_when_off(tmp_path, monkeypatch):
    rec, log_path = make_recorder(tmp_path, level="off")
    monkeypatch.setattr(metrics_module, "_recorder", rec)

    async def call_next(context):
        return "result"

    context = make_context("view", {"metrics": {"run_id": "r"}})
    assert await MetricsMiddleware().on_call_tool(context, call_next) == "result"
    assert not log_path.exists()
