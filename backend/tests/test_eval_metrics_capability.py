import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import vista_backend.metrics as metrics_module
from vista_backend.agents.eval_metrics import EvalMetricsCapability
from vista_backend.metrics import MetricsRecorder, MetricsSettings


class DummyUsage:
    requests = 2
    tool_calls = 1
    input_tokens = 11
    output_tokens = 7
    cache_read_tokens = 0
    cache_write_tokens = 0


class DummyResult:
    def usage(self) -> DummyUsage:
        return DummyUsage()


def load_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.mark.anyio
async def test_eval_metrics_capability_emits_backend_metrics(tmp_path, monkeypatch):
    log_path = tmp_path / "metrics.jsonl"
    recorder = MetricsRecorder(MetricsSettings(level="perf"), default_log_path=log_path)
    monkeypatch.setattr(metrics_module, "_recorder", recorder)

    cap = EvalMetricsCapability(
        session_id="session-1",
        project_id="project-1",
        skills_loaded=lambda: ["salt-analysis"],
        attribute_skill=lambda tool_name, args_blob: (
            "salt-analysis"
            if tool_name == "run_bash"
            and args_blob
            and "/mnt/skills/salt-analysis" in args_blob
            else None
        ),
    )
    run_cap = await cap.for_run(SimpleNamespace())

    async def run_handler():
        run_cap.note_human_intervention()
        return await run_cap.wrap_tool_execute(
            SimpleNamespace(),
            call=SimpleNamespace(),
            tool_def=SimpleNamespace(name="run_bash"),
            args={"command": "bash /mnt/skills/salt-analysis/scripts/run.sh"},
            handler=_tool_handler,
        )

    async def _tool_handler(args):
        assert "command" in args
        return DummyResult()

    result = await run_cap.wrap_run(SimpleNamespace(), handler=run_handler)
    assert isinstance(result, DummyResult)

    events = load_events(log_path)
    assert [event["event_type"] for event in events] == [
        "tool_call.client",
        "agent_run",
    ]

    tool_event = events[0]
    assert tool_event["tool_name"] == "run_bash"
    assert tool_event["status"] == "ok"
    assert tool_event["session_id"] == "session-1"
    assert tool_event["project_id"] == "project-1"
    assert tool_event["payload"]["skill"] == "salt-analysis"
    assert tool_event["payload"]["args_bytes"] > 0

    run_event = events[1]
    assert run_event["status"] == "ok"
    assert run_event["session_id"] == "session-1"
    assert run_event["project_id"] == "project-1"
    assert run_event["payload"]["stop_reason"] == "completed"
    assert run_event["payload"]["tool_calls"] == 1
    assert run_event["payload"]["human_interventions"] == 1
    assert run_event["payload"]["skills_loaded"] == ["salt-analysis"]
