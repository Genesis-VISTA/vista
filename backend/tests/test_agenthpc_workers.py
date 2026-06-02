"""
Tests for vista_backend.agents.agenthpc_workers.

We mock both the proposer LLM (so no real model call goes out) and the
ProjectAgent.call_tool MCP path (so no real MCP server is needed). What
the tests verify is the *coordination logic*:

  - The pool dispatches N workers concurrently.
  - Workers exit cleanly when ``agenthpc_get_all_results`` returns
    ``should_stop: True``.
  - The pool returns a summary in the documented shape.
  - The pool calls ``agenthpc_cancel_all_pending`` on cancellation.
"""
import asyncio
import itertools
import json
from typing import Any

import mcp.types
import pytest

from vista_backend.agents import agenthpc_workers
from vista_backend.agents.agenthpc_workers import (
    AlloyComposition,
    _parse_tool_result,
    run_alloy_workers,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _tool_result(payload: dict[str, Any]) -> mcp.types.CallToolResult:
    """Wrap a Python dict in a CallToolResult the way FastMCP would."""
    return mcp.types.CallToolResult(
        content=[mcp.types.TextContent(type="text", text=json.dumps(payload))],
        isError=False,
    )


class FakeProjectAgent:
    """Stand-in for vista_backend.agents.agents.ProjectAgent.

    Only ``call_tool`` is consulted by the worker pool. Behavior is
    parameterized per-tool via the constructor so tests can shape the
    campaign trajectory."""

    def __init__(self, handlers: dict[str, Any]) -> None:
        self._handlers = handlers
        # Per-tool call log (tool_name -> list[arguments]) for assertions.
        self.calls: dict[str, list[dict[str, Any]]] = {}

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> mcp.types.CallToolResult:
        self.calls.setdefault(name, []).append(arguments)
        handler = self._handlers.get(name)
        if handler is None:
            raise KeyError(f"FakeProjectAgent has no handler for tool {name!r}")
        payload = handler(arguments) if callable(handler) else handler
        return _tool_result(payload)


# --------------------------------------------------------------------------- helpers

def _noop_log(level: str, message: str) -> None:
    return None


def _capture_log() -> tuple[list[tuple[str, str]], Any]:
    sink: list[tuple[str, str]] = []
    def _fn(level: str, message: str) -> None:
        sink.append((level, message))
    return sink, _fn


# --------------------------------------------------------------------------- tests

@pytest.mark.anyio
async def test_pool_exits_immediately_when_should_stop_set(monkeypatch):
    """If the very first ``agenthpc_get_all_results`` says should_stop, no
    worker calls submit, and the pool returns the canned summary."""
    final_state = {
        "app_type": "monbtaw",
        "num_trials": 3,
        "best_parameters": [0.30, 0.20, 0.30, 0.20],
        "best_score": 1212.5,
        "threshold_reached": True,
        "budget_exhausted": False,
        "stopped_by_user": False,
        "trials": [
            {"parameters": [0.25, 0.25, 0.25, 0.25], "score": 1180.0},
            {"parameters": [0.30, 0.25, 0.25, 0.20], "score": 1195.2},
            {"parameters": [0.30, 0.20, 0.30, 0.20], "score": 1212.5},
        ],
        "should_stop": True,
    }

    agent = FakeProjectAgent({
        "agenthpc_get_all_results": final_state,
    })

    # Workers must never get past get_all_results, so they never propose.
    async def _should_never_propose(*args, **kwargs):
        raise AssertionError("worker called the proposer despite should_stop")
    monkeypatch.setattr(agenthpc_workers, "_propose_composition", _should_never_propose)

    summary = await run_alloy_workers(
        project_agent=agent,
        app_type="monbtaw",
        num_workers=4,
        target_score=None,
        max_trials=None,
        log=_noop_log,
    )

    assert summary["best_score"] == 1212.5
    assert summary["best_parameters"] == [0.30, 0.20, 0.30, 0.20]
    assert summary["num_trials"] == 3
    assert summary["threshold_reached"] is True
    # Every worker called get_all_results once + one final call from the
    # pool itself for the summary read.
    assert len(agent.calls["agenthpc_get_all_results"]) == 4 + 1
    assert "agenthpc_submit_parameter_set" not in agent.calls


@pytest.mark.anyio
async def test_full_trial_lifecycle_one_worker(monkeypatch):
    """One worker runs one trial end-to-end: propose -> submit -> wait ->
    result -> plot. After it scores, should_stop flips True and the loop
    exits."""
    # Single proposal queue so the worker submits exactly one composition.
    composition = AlloyComposition(Mo=0.25, Nb=0.25, Ta=0.25, W=0.25)

    async def _fixed_proposer(worker_id, num_workers, trials, log):
        return composition
    monkeypatch.setattr(agenthpc_workers, "_propose_composition", _fixed_proposer)

    # State flips: first call -> empty, allow one trial; second call ->
    # has the trial AND should_stop is True so the worker exits.
    state_seq = [
        {  # before any trial
            "num_trials": 0, "best_score": 0.0, "best_parameters": None,
            "threshold_reached": False, "budget_exhausted": False,
            "stopped_by_user": False, "should_stop": False, "trials": [],
        },
        {  # after the trial: stop signal arrives via threshold reached
            "num_trials": 1, "best_score": 1250.0,
            "best_parameters": [0.25, 0.25, 0.25, 0.25],
            "threshold_reached": True, "budget_exhausted": False,
            "stopped_by_user": False, "should_stop": True,
            "trials": [{"parameters": [0.25, 0.25, 0.25, 0.25], "score": 1250.0}],
        },
        {  # final summary read by run_alloy_workers
            "num_trials": 1, "best_score": 1250.0,
            "best_parameters": [0.25, 0.25, 0.25, 0.25],
            "threshold_reached": True, "budget_exhausted": False,
            "stopped_by_user": False, "should_stop": True,
            "trials": [{"parameters": [0.25, 0.25, 0.25, 0.25], "score": 1250.0}],
        },
    ]
    state_iter = iter(state_seq)

    def _state_handler(args):
        return next(state_iter)

    agent = FakeProjectAgent({
        "agenthpc_get_all_results": _state_handler,
        "agenthpc_submit_parameter_set": {
            "cached": False, "job_id": "job-0",
            "log_params": {"trial_number": 0},
            "parameters": [0.25, 0.25, 0.25, 0.25],
        },
        "agenthpc_wait_for_job": {"status": "completed", "job_id": "job-0"},
        "agenthpc_get_job_result": {
            "status": "success", "score": 1250.0, "parameters": [0.25, 0.25, 0.25, 0.25],
            "threshold_reached": True, "score_threshold": 1200.0,
        },
        "agenthpc_plot_progress": {"_text": "Plot saved to /mnt/data/output/alloy-progress/monbtaw_progress.png"},
    })

    summary = await run_alloy_workers(
        project_agent=agent,
        app_type="monbtaw",
        num_workers=1,
        target_score=1200.0,
        max_trials=10,
        log=_noop_log,
    )

    assert summary["best_score"] == 1250.0
    assert summary["threshold_reached"] is True
    # Submit and result were called exactly once each.
    assert len(agent.calls["agenthpc_submit_parameter_set"]) == 1
    assert len(agent.calls["agenthpc_get_job_result"]) == 1
    # The worker called plot_progress per the per-trial-plot contract.
    assert len(agent.calls["agenthpc_plot_progress"]) == 1


@pytest.mark.anyio
async def test_claim_lost_reproposes_without_extra_submit(monkeypatch):
    """If submit returns claim_lost, the worker must NOT call wait/result
    for that proposal — it should loop back and propose again."""
    # Two proposals: first one will be told "claim_lost", second succeeds.
    propose_calls = {"n": 0}
    async def _proposer(worker_id, num_workers, trials, log):
        propose_calls["n"] += 1
        if propose_calls["n"] == 1:
            return AlloyComposition(Mo=0.25, Nb=0.25, Ta=0.25, W=0.25)
        return AlloyComposition(Mo=0.30, Nb=0.20, Ta=0.30, W=0.20)
    monkeypatch.setattr(agenthpc_workers, "_propose_composition", _proposer)

    state_seq = [
        # First iteration: empty state.
        {"num_trials": 0, "best_score": 0.0, "best_parameters": None,
         "threshold_reached": False, "budget_exhausted": False,
         "stopped_by_user": False, "should_stop": False, "trials": []},
        # Second iteration after claim_lost: still empty; propose again.
        {"num_trials": 0, "best_score": 0.0, "best_parameters": None,
         "threshold_reached": False, "budget_exhausted": False,
         "stopped_by_user": False, "should_stop": False, "trials": []},
        # Third iteration: trial done, stop.
        {"num_trials": 1, "best_score": 1230.0,
         "best_parameters": [0.30, 0.20, 0.30, 0.20],
         "threshold_reached": True, "budget_exhausted": False,
         "stopped_by_user": False, "should_stop": True,
         "trials": [{"parameters": [0.30, 0.20, 0.30, 0.20], "score": 1230.0}]},
        # Final summary read.
        {"num_trials": 1, "best_score": 1230.0,
         "best_parameters": [0.30, 0.20, 0.30, 0.20],
         "threshold_reached": True, "budget_exhausted": False,
         "stopped_by_user": False, "should_stop": True,
         "trials": [{"parameters": [0.30, 0.20, 0.30, 0.20], "score": 1230.0}]},
    ]
    state_iter = iter(state_seq)

    submit_calls = {"n": 0}
    def _submit_handler(args):
        submit_calls["n"] += 1
        if submit_calls["n"] == 1:
            return {"claim_lost": True, "trial_number": 0,
                    "parameters": [0.25, 0.25, 0.25, 0.25]}
        return {"cached": False, "job_id": "job-0",
                "log_params": {"trial_number": 0},
                "parameters": [0.30, 0.20, 0.30, 0.20]}

    agent = FakeProjectAgent({
        "agenthpc_get_all_results": lambda args: next(state_iter),
        "agenthpc_submit_parameter_set": _submit_handler,
        "agenthpc_wait_for_job": {"status": "completed", "job_id": "job-0"},
        "agenthpc_get_job_result": {
            "status": "success", "score": 1230.0,
            "parameters": [0.30, 0.20, 0.30, 0.20],
            "threshold_reached": True, "score_threshold": 1200.0,
        },
        "agenthpc_plot_progress": {"_text": "Plot saved to /tmp/x.png"},
    })

    summary = await run_alloy_workers(
        project_agent=agent,
        app_type="monbtaw",
        num_workers=1,
        target_score=1200.0,
        max_trials=10,
        log=_noop_log,
    )

    assert submit_calls["n"] == 2  # claim_lost + retry
    # Only the second submit went through to wait/result.
    assert len(agent.calls["agenthpc_wait_for_job"]) == 1
    assert len(agent.calls["agenthpc_get_job_result"]) == 1
    assert summary["best_score"] == 1230.0


@pytest.mark.anyio
async def test_invalid_num_workers_raises(monkeypatch):
    agent = FakeProjectAgent({})
    with pytest.raises(ValueError, match="num_workers"):
        await run_alloy_workers(
            project_agent=agent,
            app_type="monbtaw",
            num_workers=0,
            target_score=None,
            max_trials=None,
            log=_noop_log,
        )


@pytest.mark.anyio
async def test_cancellation_invokes_cancel_all_pending(monkeypatch):
    """When the worker pool is cancelled (e.g. UI cancels the chat run),
    it must call agenthpc_cancel_all_pending so no Slurm jobs are left
    orphaned, then re-raise CancelledError."""
    # Make the proposer block forever so the worker never finishes a trial.
    started = asyncio.Event()

    async def _hanging_proposer(worker_id, num_workers, trials, log):
        started.set()
        await asyncio.sleep(3600)  # blocks until cancellation propagates
        raise AssertionError("proposer was not cancelled")
    monkeypatch.setattr(agenthpc_workers, "_propose_composition", _hanging_proposer)

    agent = FakeProjectAgent({
        "agenthpc_get_all_results": {
            "num_trials": 0, "best_score": 0.0, "best_parameters": None,
            "threshold_reached": False, "budget_exhausted": False,
            "stopped_by_user": False, "should_stop": False, "trials": [],
        },
        "agenthpc_cancel_all_pending": {"num_cancelled": 0, "cancelled": [], "stop_flag_set": True},
    })

    pool_task = asyncio.create_task(run_alloy_workers(
        project_agent=agent,
        app_type="monbtaw",
        num_workers=2,
        target_score=None,
        max_trials=None,
        log=_noop_log,
    ))

    await started.wait()
    pool_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pool_task

    # The cleanup hook in run_alloy_workers must have called cancel_all_pending.
    assert "agenthpc_cancel_all_pending" in agent.calls
    assert agent.calls["agenthpc_cancel_all_pending"][0] == {"app_type": "monbtaw"}


def test_parse_tool_result_unwraps_json_dict():
    payload = {"best_score": 1234.5, "num_trials": 7}
    result = _parse_tool_result("foo", _tool_result(payload))
    assert result == payload


def test_parse_tool_result_wraps_non_json_text():
    """Some MCP tools (like agenthpc_plot_progress) return plain strings.
    _parse_tool_result should not blow up — it wraps the text in a dict."""
    plain = mcp.types.CallToolResult(
        content=[mcp.types.TextContent(type="text", text="Plot saved to /tmp/x.png")],
        isError=False,
    )
    result = _parse_tool_result("agenthpc_plot_progress", plain)
    assert result == {"_text": "Plot saved to /tmp/x.png"}


def test_parse_tool_result_raises_on_error():
    err = mcp.types.CallToolResult(
        content=[mcp.types.TextContent(type="text", text="boom")],
        isError=True,
    )
    with pytest.raises(RuntimeError, match="boom"):
        _parse_tool_result("agenthpc_get_all_results", err)
