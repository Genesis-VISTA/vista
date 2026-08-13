"""
Tests for the metrics core (evaluation plan M1) and the client-side
tool-call probe (M2): level/override gating, the off-is-a-no-op contract,
deterministic per-run sampling, correlation-id stamping, and the JSONL
schema consumed by `scripts/metrics_report.py`.
"""

import json
import uuid
from pathlib import Path

import pytest

import vista_backend.metrics as metrics_module
from vista_backend.agents.eval_metrics import EvalMetricsCapability
from vista_backend.metrics import (
    MetricsRecorder,
    MetricsSettings,
    run_context,
    timer,
)


def make_recorder(
    tmp_path: Path, level: str = "off", **kwargs
) -> tuple[MetricsRecorder, Path]:
    log_path = tmp_path / "metrics.jsonl"
    settings = MetricsSettings(level=level, **kwargs)
    return MetricsRecorder(settings, default_log_path=log_path), log_path


def read_events(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines()]


# ---------------------------------------------------------------------------
# Level / override gating
# ---------------------------------------------------------------------------


def test_off_is_noop(tmp_path):
    rec, log_path = make_recorder(tmp_path, level="off")
    assert rec.enabled is False
    assert rec.tool_call(tool_name="t", duration_ms=1.0) is None
    assert rec.agent_run(duration_ms=1.0, stop_reason="completed") is None
    assert rec.stage(stage="rag.embed", duration_ms=1.0) is None
    assert not log_path.exists()


def test_off_is_master_kill_over_overrides(tmp_path):
    """Granular `on` overrides must not resurrect a `level=off` recorder."""
    rec, log_path = make_recorder(
        tmp_path, level="off", tool_timing="on", agent_run="on"
    )
    assert rec.active("prod", "tool_timing") is False
    assert rec.tool_call(tool_name="t", duration_ms=1.0) is None
    assert not log_path.exists()


def test_prod_emits_tool_call_and_agent_run_but_not_stages(tmp_path):
    rec, log_path = make_recorder(tmp_path, level="prod", sample_rate=1.0)
    assert rec.tool_call(tool_name="rag_search", duration_ms=12.5) is not None
    assert rec.agent_run(duration_ms=100.0, stop_reason="completed") is not None
    assert rec.stage(stage="rag.embed", duration_ms=3.0) is None  # perf-only
    events = read_events(log_path)
    assert [e["event_type"] for e in events] == ["tool_call.client", "agent_run"]


def test_override_lifts_probe_above_level(tmp_path):
    """The E13/E14 production recipe: LEVEL=prod + SKILL_USAGE/STAGE on."""
    rec, log_path = make_recorder(
        tmp_path, level="prod", sample_rate=1.0, stage_timing="on"
    )
    assert rec.stage(stage="rag.embed", duration_ms=3.0) is not None
    assert read_events(log_path)[0]["event_type"] == "stage.rag.embed"


def test_override_off_suppresses_probe(tmp_path):
    rec, log_path = make_recorder(tmp_path, level="perf", tool_timing="off")
    assert rec.tool_call(tool_name="t", duration_ms=1.0) is None
    # Other perf probes unaffected.
    assert rec.stage(stage="rag.embed", duration_ms=3.0) is not None


def test_settings_env_parsing(monkeypatch):
    monkeypatch.setenv("VISTA_BACKEND_METRICS__LEVEL", "perf")
    monkeypatch.setenv("VISTA_BACKEND_METRICS__SAMPLE_RATE", "0.5")
    monkeypatch.setenv("VISTA_BACKEND_METRICS__TOOL_TIMING", "off")
    from vista_backend.config import Settings

    settings = Settings()
    assert settings.metrics.level == "perf"
    assert settings.metrics.sample_rate == 0.5
    assert settings.metrics.tool_timing == "off"


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


def test_sampling_is_deterministic_per_run(tmp_path):
    rec, _ = make_recorder(tmp_path, level="prod", sample_rate=0.5)
    for _ in range(20):
        run_id = uuid.uuid4().hex
        assert rec.sampled(run_id) == rec.sampled(run_id)


def test_sample_rate_zero_and_one(tmp_path):
    rec_all, log_all = make_recorder(tmp_path, level="perf")  # perf default 1.0
    rec_none, _ = make_recorder(tmp_path, level="prod", sample_rate=0.0)
    with run_context(session_id="s", project_id="p"):
        assert rec_all.tool_call(tool_name="t", duration_ms=1.0) is not None
        assert rec_none.tool_call(tool_name="t", duration_ms=1.0) is None
    assert len(read_events(log_all)) == 1


def test_run_sampling_keeps_runs_whole(tmp_path):
    """All events of one run share the sampling decision (offline joins)."""
    rec, _ = make_recorder(tmp_path, level="prod", sample_rate=0.5)
    for _ in range(20):
        with run_context(session_id="s", project_id="p"):
            decisions = {
                rec.tool_call(tool_name="a", duration_ms=1.0) is not None,
                rec.tool_call(tool_name="b", duration_ms=1.0) is not None,
                rec.agent_run(duration_ms=5.0, stop_reason="completed") is not None,
            }
        assert len(decisions) == 1


# ---------------------------------------------------------------------------
# Correlation ids + schema
# ---------------------------------------------------------------------------


def test_run_context_stamps_ids_and_resets(tmp_path):
    rec, log_path = make_recorder(tmp_path, level="perf")
    with run_context(session_id="proj-user", project_id="proj") as run_id:
        rec.tool_call(tool_name="run_bash", duration_ms=7.0)
    rec.tool_call(tool_name="outside", duration_ms=1.0)

    inside, outside = read_events(log_path)
    assert inside["run_id"] == run_id
    assert inside["session_id"] == "proj-user"
    assert inside["project_id"] == "proj"
    assert "run_id" not in outside


def test_event_schema_matches_plan(tmp_path):
    """Pin the §5 JSONL schema: ts/event_type/status always present,
    correlation ids and duration when known, extras under payload."""
    rec, log_path = make_recorder(tmp_path, level="perf")
    with run_context(session_id="s", project_id="p"):
        rec.tool_call(
            tool_name="agenthpc_submit", duration_ms=412.7, payload={"args_bytes": 312}
        )
    (event,) = read_events(log_path)
    assert set(event) == {
        "ts",
        "event_type",
        "status",
        "session_id",
        "run_id",
        "project_id",
        "tool_name",
        "duration_ms",
        "payload",
    }
    assert event["ts"].endswith("Z")
    assert event["payload"] == {"args_bytes": 312}


def test_agent_run_event_payload(tmp_path):
    class FakeUsage:
        requests = 3
        tool_calls = 5
        input_tokens = 1000
        output_tokens = 200
        cache_read_tokens = 50
        cache_write_tokens = 10

    rec, log_path = make_recorder(tmp_path, level="prod", sample_rate=1.0)
    rec.agent_run(
        duration_ms=1234.5,
        stop_reason="completed",
        usage=FakeUsage(),
        tool_calls=5,
        human_interventions=1,
        skills_loaded=["alloy-design"],
    )
    (event,) = read_events(log_path)
    assert event["event_type"] == "agent_run"
    assert event["status"] == "ok"
    payload = event["payload"]
    assert payload["stop_reason"] == "completed"
    assert payload["iterations"] == 3
    assert payload["tokens"]["input"] == 1000
    assert payload["human_interventions"] == 1
    assert payload["skills_loaded"] == ["alloy-design"]


def test_agent_run_failure_status(tmp_path):
    rec, log_path = make_recorder(tmp_path, level="prod", sample_rate=1.0)
    rec.agent_run(duration_ms=10.0, stop_reason="budget_exhausted")
    (event,) = read_events(log_path)
    assert event["status"] == "budget_exhausted"
    assert event["payload"]["stop_reason"] == "budget_exhausted"


def test_timer_measures_monotonic_duration():
    with timer() as t:
        pass
    assert t.duration_ms is not None and t.duration_ms >= 0.0


def test_write_failure_degrades_without_raising(tmp_path):
    bad_path = tmp_path / "not-a-dir"
    bad_path.write_text("file, not a directory")
    settings = MetricsSettings(level="perf", log_path=bad_path / "metrics.jsonl")
    rec = MetricsRecorder(settings)
    assert rec.tool_call(tool_name="t", duration_ms=1.0) is not None  # no raise
    assert rec._write_failed is True


# ---------------------------------------------------------------------------
# M2: the hook-owned backend tool-call probe
# ---------------------------------------------------------------------------


@pytest.fixture
def project_agent(alice):
    from vista_backend.agents.agents import ProjectAgent
    from vista_backend.db.schemas import ProjectPublic

    project = ProjectPublic(id=uuid.uuid4(), name="metrics-test")
    return ProjectAgent(project, alice, uuid.uuid4())


async def fake_call_tool_factory(captured: dict):
    async def fake_call_tool(name, args, metadata):
        captured["name"] = name
        captured["metadata"] = metadata
        return {"ok": True}

    return fake_call_tool


async def run_with_eval_metrics(
    agent,
    handler,
    *,
    session_id: str | None = None,
    project_id: str | None = None,
):
    capability = EvalMetricsCapability(
        session_id=session_id or agent.id,
        project_id=project_id or str(agent.project.id),
        skills_loaded=lambda: list(agent.project.skills),
        attribute_skill=agent._attribute_skill,
    )
    return await capability.wrap_run(None, handler=handler)


async def execute_tool_with_eval_metrics(
    agent, *, captured: dict, tool_name: str, args: dict[str, object]
):
    callback = agent._make_mcp_process_tool_call()
    capability = EvalMetricsCapability(
        session_id=agent.id,
        project_id=str(agent.project.id),
        skills_loaded=lambda: list(agent.project.skills),
        attribute_skill=agent._attribute_skill,
    )

    async def tool_handler(validated_args):
        return await callback(
            None, await fake_call_tool_factory(captured), tool_name, validated_args
        )

    async def run_handler():
        return await capability.wrap_tool_execute(
            None,
            call=None,
            tool_def=type("ToolDef", (), {"name": tool_name})(),
            args=args,
            handler=tool_handler,
        )

    return await capability.wrap_run(None, handler=run_handler)


@pytest.mark.anyio
async def test_process_tool_call_noop_when_off(tmp_path, monkeypatch, project_agent):
    """Off contract: no timing event, and the MCP metadata is byte-identical
    to an uninstrumented build (no `metrics` key on the wire)."""
    rec, log_path = make_recorder(tmp_path, level="off")
    monkeypatch.setattr(metrics_module, "_recorder", rec)

    captured: dict = {}
    callback = project_agent._make_mcp_process_tool_call()
    result = await callback(
        None, await fake_call_tool_factory(captured), "view", {"path": "x"}
    )

    assert result == {"ok": True}
    assert "metrics" not in captured["metadata"]["vista"]
    assert not log_path.exists()


@pytest.mark.anyio
async def test_process_tool_call_times_and_correlates(
    tmp_path, monkeypatch, project_agent
):
    rec, log_path = make_recorder(tmp_path, level="perf")
    monkeypatch.setattr(metrics_module, "_recorder", rec)

    captured: dict = {}
    await execute_tool_with_eval_metrics(
        project_agent,
        captured=captured,
        tool_name="view",
        args={"path": "x"},
    )

    # Correlation ids ride along to the MCP server for M3 joins.
    run_id = captured["metadata"]["vista"]["metrics"]["run_id"]
    assert run_id
    events = read_events(log_path)
    tool_events = [e for e in events if e["event_type"] == "tool_call.client"]
    assert len(tool_events) == 1
    (event,) = tool_events
    assert event["event_type"] == "tool_call.client"
    assert event["tool_name"] == "view"
    assert event["status"] == "ok"
    assert event["run_id"] == run_id
    assert event["duration_ms"] >= 0.0
    assert event["payload"]["args_bytes"] > 0


@pytest.mark.anyio
async def test_skill_attribution_by_script_path(tmp_path, monkeypatch, alice):
    """M11/E14: a sandbox call invoking a loaded skill's files is tagged
    with that skill; ambiguous or unrelated calls stay untagged."""
    from vista_backend.agents.agents import ProjectAgent
    from vista_backend.db.schemas import ProjectPublic

    project = ProjectPublic(id=uuid.uuid4(), name="t", skills=["salt-analysis"])
    agent = ProjectAgent(project, alice, uuid.uuid4())
    rec, log_path = make_recorder(tmp_path, level="perf")
    monkeypatch.setattr(metrics_module, "_recorder", rec)

    async def run_tool(cmd: str):
        cap = EvalMetricsCapability(
            session_id=agent.id,
            project_id=str(agent.project.id),
            skills_loaded=lambda: list(agent.project.skills),
            attribute_skill=agent._attribute_skill,
        )

        async def ok_handler(args):
            return {"ok": True}

        return await cap.wrap_tool_execute(
            None,
            call=None,
            tool_def=type("ToolDef", (), {"name": "run_bash"})(),
            args={"cmd": cmd},
            handler=ok_handler,
        )

    await run_tool("python /mnt/skills/salt-analysis/scripts/analyze_salt.py")
    await run_tool("ls /mnt/data")

    tagged, untagged = read_events(log_path)
    assert tagged["payload"]["skill"] == "salt-analysis"
    assert "skill" not in untagged["payload"]


@pytest.mark.anyio
async def test_skill_attribution_by_allowed_tools(tmp_path, monkeypatch, project_agent):
    """Unambiguous allowed_tools patterns attribute; ambiguity does not.
    Uses the E13/E14 production recipe (LEVEL=prod + SKILL_USAGE=on), where
    skill tags are emitted without profiling-grade args_bytes."""
    rec, log_path = make_recorder(
        tmp_path, level="prod", sample_rate=1.0, skill_usage="on"
    )
    monkeypatch.setattr(metrics_module, "_recorder", rec)

    project_agent._skill_allowed_tools = {"alloy-design": "agenthpc_*"}
    cap = EvalMetricsCapability(
        session_id=project_agent.id,
        project_id=str(project_agent.project.id),
        skills_loaded=lambda: list(project_agent.project.skills),
        attribute_skill=project_agent._attribute_skill,
    )

    async def ok_handler(args):
        return {"ok": True}

    await cap.wrap_tool_execute(
        None,
        call=None,
        tool_def=type("ToolDef", (), {"name": "agenthpc_submit"})(),
        args={"composition": "MoNbTaW"},
        handler=ok_handler,
    )

    project_agent._skill_allowed_tools = {
        "alloy-design": "agenthpc_*",
        "other": "agenthpc_*",
    }
    await cap.wrap_tool_execute(
        None,
        call=None,
        tool_def=type("ToolDef", (), {"name": "agenthpc_submit"})(),
        args={"composition": "MoNbTaW"},
        handler=ok_handler,
    )

    unambiguous, ambiguous = read_events(log_path)
    assert unambiguous["payload"] == {"skill": "alloy-design"}  # no args_bytes at prod
    assert "payload" not in ambiguous


@pytest.mark.anyio
async def test_process_tool_call_records_errors(tmp_path, monkeypatch, project_agent):
    rec, log_path = make_recorder(tmp_path, level="prod", sample_rate=1.0)
    monkeypatch.setattr(metrics_module, "_recorder", rec)

    async def failing_handler(args):
        raise RuntimeError("boom")

    cap = EvalMetricsCapability(
        session_id=project_agent.id,
        project_id=str(project_agent.project.id),
        skills_loaded=lambda: list(project_agent.project.skills),
        attribute_skill=project_agent._attribute_skill,
    )
    with pytest.raises(RuntimeError):
        await cap.wrap_tool_execute(
            None,
            call=None,
            tool_def=type("ToolDef", (), {"name": "run_bash"})(),
            args={"cmd": "x"},
            handler=failing_handler,
        )

    (event,) = read_events(log_path)
    assert event["status"] == "error"
    assert event["tool_name"] == "run_bash"
