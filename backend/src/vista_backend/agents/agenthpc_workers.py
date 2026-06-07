"""
Backend-side worker pool for parallel agenthpc optimization.

Spawns N independent PydanticAI sub-Agent runs, each proposing one
composition per iteration and coordinating via the shared MCP server state
(per-session ``_results_cache``, claim sentinels, and the stop flag).
Mirrors ``AgentHPC/main.py``'s threaded model in asyncio: every worker has
its own LLM conversation; the cache + claim sentinel play the role of the
shared ``tried`` dict and ``threading.Lock``.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

import mcp.types
from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent
from pydantic_ai.models import infer_model

from ..config import settings

if TYPE_CHECKING:
    from .agents import ProjectAgent


# Forward-declared callable used by the pool to push log lines into the
# enclosing run_stream's StreamMerger. Signature: (level, message) -> None.
LogFn = Callable[[str, str], None]

# Push a per-trial "report back" line into the UI's intermediate-bubble
# (agent thinking) channel — each call produces one standalone bubble that
# the user can read live and that collapses once the agent's final response
# arrives. Signature: (text) -> None.
ProgressFn = Callable[[str], None]

# Synthesize a (function_tool_call, function_tool_result) pair for an MCP
# tool the chat agent did not directly invoke (used here for display_file at
# the end of the pool, so the UI auto-renders the final plot just like in
# sequential mode). Signature: (tool_name, args, content) -> None.
ToolEventFn = Callable[[str, dict[str, Any], Any], None]


def _noop_progress(text: str) -> None:
    return None


def _noop_tool_event(tool_name: str, args: dict[str, Any], content: Any) -> None:
    return None


WORKER_PROMPT = (Path(__file__).parent / "alloy_worker_prompt.md").read_text()


class AlloyComposition(BaseModel):
    """Atom fractions of Mo, Nb, Ta, W — must sum to 1.0 within 1e-3."""

    Mo: float = Field(ge=0.0, le=1.0)
    Nb: float = Field(ge=0.0, le=1.0)
    Ta: float = Field(ge=0.0, le=1.0)
    W: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _sum_to_one(self) -> "AlloyComposition":
        total = self.Mo + self.Nb + self.Ta + self.W
        if abs(total - 1.0) > 1e-3:
            raise ValueError(
                f"Composition must sum to 1.0; got {total:.4f} "
                f"[Mo={self.Mo}, Nb={self.Nb}, Ta={self.Ta}, W={self.W}]"
            )
        return self

    def as_list(self) -> list[float]:
        return [self.Mo, self.Nb, self.Ta, self.W]

    def fmt(self) -> str:
        return f"({self.Mo:.2f}, {self.Nb:.2f}, {self.Ta:.2f}, {self.W:.2f})"


def _parse_tool_result(name: str, result: mcp.types.CallToolResult) -> dict[str, Any]:
    """Extract a JSON dict from a CallToolResult. FastMCP serializes dict
    returns as a single TextContent; we parse it back here so the pool can
    work with plain Python dicts rather than MCP envelopes."""
    if result.isError:
        msg = ""
        for c in result.content:
            if isinstance(c, mcp.types.TextContent):
                msg = c.text
                break
        raise RuntimeError(f"agenthpc tool {name!r} returned error: {msg}")
    for c in result.content:
        if isinstance(c, mcp.types.TextContent):
            try:
                return json.loads(c.text)
            except json.JSONDecodeError:
                # Some tools return plain strings (e.g. plot_progress) — wrap.
                return {"_text": c.text}
    return {}


def _build_proposer_agent() -> Agent[None, AlloyComposition]:
    """One PydanticAI Agent reused across proposal calls (it is stateless —
    each .run() starts a fresh conversation)."""
    return Agent(
        model=infer_model(settings.model),
        system_prompt=WORKER_PROMPT,
        output_type=AlloyComposition,
    )


_PROPOSER_AGENT: Agent[None, AlloyComposition] | None = None


def _proposer() -> Agent[None, AlloyComposition]:
    global _PROPOSER_AGENT
    if _PROPOSER_AGENT is None:
        _PROPOSER_AGENT = _build_proposer_agent()
    return _PROPOSER_AGENT


def _format_trial_block(trials: list[dict]) -> str:
    """Render the worker-visible trial history. Caps the list so the prompt
    stays bounded once the campaign runs long."""
    if not trials:
        return "  (no trials yet)"
    # Show all if small, otherwise first 3, best, last 10.
    if len(trials) <= 15:
        rows = trials
    else:
        best = max(trials, key=lambda t: t.get("score", 0.0))
        rows = trials[:3] + [best] + trials[-10:]
    lines: list[str] = []
    for t in rows:
        p = t.get("parameters", [])
        s = t.get("score", 0.0)
        if len(p) == 4:
            lines.append(
                f"  - ({p[0]:.2f}, {p[1]:.2f}, {p[2]:.2f}, {p[3]:.2f}) -> Tc = {s:.1f}"
            )
    return "\n".join(lines) or "  (no parseable trials)"


async def _propose_composition(
    worker_id: int, num_workers: int, trials: list[dict], log: LogFn,
) -> AlloyComposition | None:
    """Run one LLM call to propose one composition. Returns ``None`` on
    persistent LLM error — the caller backs off briefly and retries."""
    trial_block = _format_trial_block(trials)
    user_prompt = (
        f"You are worker {worker_id} of {num_workers}.\n"
        f"Tried compositions in this session (worker-shared state):\n"
        f"{trial_block}\n\n"
        f"Propose ONE new composition (Mo, Nb, Ta, W). It must sum to 1.0 "
        f"and must not duplicate any composition above."
    )
    try:
        result = await _proposer().run(user_prompt)
        return result.output
    except Exception as e:
        log("WARNING", f"Worker[{worker_id}] LLM proposal failed: {e}")
        return None


# Sandbox URI of the cumulative plot the workers refresh after every score.
# Matches agenthpc.mcp._PLOT_SANDBOX_DIR + the per-app filename, kept in sync
# manually because the MCP server doesn't expose this path through a tool.
_ALLOY_PLOT_URI = "/mnt/data/output/alloy-progress/monbtaw_progress.png"


async def _show_alloy_plot(
    project_agent: "ProjectAgent",
    emit_tool_event: ToolEventFn,
    log: LogFn,
    worker_id: int | None = None,
) -> None:
    """Pull the current plot's HTML through ``display_file`` and synthesize a
    function-tool-result event so the chat UI's image renderer fires. Used
    after every worker's per-trial ``agenthpc_plot_progress`` call so the
    figure refreshes live, exactly like sequential mode. Best-effort — a
    failure here must never block trial progress."""
    try:
        display_result = _parse_tool_result(
            "display_file",
            await project_agent.call_tool("display_file", {"uri": _ALLOY_PLOT_URI}),
        )
        # display_file's HTML can come back as a wrapped {"_text": html}
        # (string return), a {"ui": {"kind": "html", "html": ...}} dict, or
        # the raw HTML dict shape returned directly by the tool.
        html_content = display_result.get("_text") or display_result
        emit_tool_event("display_file", {"uri": _ALLOY_PLOT_URI}, html_content)
    except Exception as e:
        who = f"Worker[{worker_id}] " if worker_id is not None else ""
        log("WARNING", f"{who}plot display refresh failed: {e}")


async def _worker_loop(
    worker_id: int,
    num_workers: int,
    project_agent: "ProjectAgent",
    app_type: str,
    target_score: float | None,
    max_trials: int | None,
    log: LogFn,
    progress: ProgressFn,
    emit_tool_event: ToolEventFn,
) -> None:
    """One worker's main loop. Each iteration: read shared state → propose →
    claim+submit → wait → score. Exits when ``should_stop`` is True."""
    log("INFO", f"Worker[{worker_id}] started")
    consecutive_proposal_failures = 0

    while True:
        # Step 1 — read shared state and decide whether to stop.
        state = _parse_tool_result(
            "agenthpc_get_all_results",
            await project_agent.call_tool(
                "agenthpc_get_all_results",
                {
                    "app_type": app_type,
                    "target_score": target_score,
                    "max_trials": max_trials,
                },
            ),
        )
        if state.get("should_stop"):
            log("INFO", f"Worker[{worker_id}] should_stop=True, exiting")
            return

        # Step 2 — propose a new composition.
        composition = await _propose_composition(
            worker_id, num_workers, state.get("trials", []), log,
        )
        if composition is None:
            consecutive_proposal_failures += 1
            # Exponential-ish backoff, capped, to avoid hot-looping when the
            # LLM endpoint is having a bad time. Each worker is independent
            # so this only stalls one of the N.
            await asyncio.sleep(min(2.0 * consecutive_proposal_failures, 30.0))
            continue
        consecutive_proposal_failures = 0

        # Step 3 — try to claim + submit. The MCP server's lock + sentinel
        # makes this atomic across workers.
        submit = _parse_tool_result(
            "agenthpc_submit_parameter_set",
            await project_agent.call_tool(
                "agenthpc_submit_parameter_set",
                {"app_type": app_type, "parameters": composition.as_list()},
            ),
        )
        if submit.get("claim_lost"):
            log(
                "INFO",
                f"Worker[{worker_id}] lost claim on {composition.fmt()} — "
                "another worker has it; re-proposing",
            )
            continue
        if submit.get("cached"):
            log(
                "INFO",
                f"Worker[{worker_id}] cache hit on {composition.fmt()} -> "
                f"Tc = {submit.get('score', 0.0):.1f}",
            )
            continue

        job_id = submit.get("job_id")
        log_params = submit.get("log_params") or {}
        if not job_id:
            log("WARNING", f"Worker[{worker_id}] submit returned no job_id: {submit}")
            continue
        submit_msg = (
            f"🚀 Worker {worker_id}/{num_workers} submitted job `{job_id}` for "
            f"composition {composition.fmt()}"
        )
        log("INFO", submit_msg)
        progress(submit_msg)

        # Step 4 — wait for completion.
        wait_resp = _parse_tool_result(
            "agenthpc_wait_for_job",
            await project_agent.call_tool(
                "agenthpc_wait_for_job",
                {
                    "app_type": app_type,
                    "job_id": job_id,
                    "log_params": log_params,
                },
            ),
        )
        wait_status = wait_resp.get("status")
        if wait_status == "no_log":
            log(
                "WARNING",
                f"Worker[{worker_id}] job {job_id} left queue with no log; moving on",
            )
            continue
        if wait_resp.get("timed_out"):
            log(
                "WARNING",
                f"Worker[{worker_id}] job {job_id} timed out; moving on",
            )
            continue
        if wait_status != "completed":
            log(
                "WARNING",
                f"Worker[{worker_id}] unexpected wait status {wait_status!r} for "
                f"job {job_id}; moving on",
            )
            continue

        # Step 5 — read the score (also clears the sentinel server-side).
        result = _parse_tool_result(
            "agenthpc_get_job_result",
            await project_agent.call_tool(
                "agenthpc_get_job_result",
                {
                    "app_type": app_type,
                    "job_id": job_id,
                    "log_params": log_params,
                    "parameters": composition.as_list(),
                    "target_score": target_score,
                },
            ),
        )
        if result.get("status") == "no_log":
            log(
                "WARNING",
                f"Worker[{worker_id}] result fetch reported no_log for job {job_id}",
            )
            continue
        score = result.get("score", 0.0)
        threshold_reached = result.get("threshold_reached")
        done_msg = (
            f"✅ Worker {worker_id}/{num_workers} completed job `{job_id}`: "
            f"{composition.fmt()} → Tc = **{score:.1f} K**"
            + ("  · 🎯 threshold reached" if threshold_reached else "")
        )
        log("INFO", done_msg)
        progress(done_msg)

        # Step 6 — refresh the cumulative Cv(T) plot so the user sees the
        # campaign evolve in the output panel. Concurrent worker calls are
        # serialized inside agenthpc_plot_progress by a process-wide lock,
        # so this is safe even with many workers finishing close together.
        try:
            await project_agent.call_tool(
                "agenthpc_plot_progress", {"app_type": app_type},
            )
        except Exception as e:
            # Plot failures must never block trial progress.
            log("WARNING", f"Worker[{worker_id}] plot refresh failed: {e}")
            continue

        # Step 7 — push the refreshed PNG to the chat UI's image renderer
        # by synthesizing a display_file tool-result event. Without this
        # the agent's run_stream never sees the per-trial plot updates —
        # only the file on disk would change. Concurrent workers may both
        # land here near-simultaneously; the UI keeps only the latest
        # rendering, which is the right behavior (latest data wins).
        await _show_alloy_plot(project_agent, emit_tool_event, log, worker_id)


async def run_alloy_workers(
    project_agent: "ProjectAgent",
    app_type: str,
    num_workers: int,
    target_score: float | None,
    max_trials: int | None,
    log: LogFn,
    progress: ProgressFn = _noop_progress,
    emit_tool_event: ToolEventFn = _noop_tool_event,
) -> dict[str, Any]:
    """Run ``num_workers`` independent worker loops concurrently and return a
    final summary when the campaign stops (threshold, budget, or stop flag).

    ``progress`` is used to surface per-trial submit / complete events to the
    chat UI as intermediate "agent thinking" bubbles, mirroring the per-trial
    visibility users get in sequential mode.

    ``emit_tool_event`` is invoked once at the end with ``display_file``'s
    HTML output so the UI's image renderer fires automatically — again
    matching the sequential-mode UX without forcing the chat agent to call
    ``display_file`` itself after this blocking tool returns.

    On ``asyncio.CancelledError`` (the chat agent run was cancelled by the UI),
    cancels every in-flight Slurm job through ``agenthpc_cancel_all_pending``
    before re-raising — so the user is left without orphan jobs.
    """
    if num_workers < 1:
        raise ValueError(f"num_workers must be >= 1, got {num_workers}")

    start_msg = (
        f"🧵 Starting parallel optimization with **{num_workers} workers** "
        f"for `{app_type}` (target Tc = {target_score}, max trials = {max_trials})."
    )
    log("INFO", start_msg)
    progress(start_msg)
    tasks = [
        asyncio.create_task(
            _worker_loop(
                worker_id=i + 1,
                num_workers=num_workers,
                project_agent=project_agent,
                app_type=app_type,
                target_score=target_score,
                max_trials=max_trials,
                log=log,
                progress=progress,
                emit_tool_event=emit_tool_event,
            ),
            name=f"agenthpc-worker-{i + 1}",
        )
        for i in range(num_workers)
    ]

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        log("WARNING", "Worker pool cancelled — cancelling in-flight Slurm jobs")
        for t in tasks:
            t.cancel()
        # Best-effort wait so workers' own try/finally can run.
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await project_agent.call_tool(
                "agenthpc_cancel_all_pending", {"app_type": app_type},
            )
        except Exception as e:
            log("ERROR", f"agenthpc_cancel_all_pending failed during cleanup: {e}")
        raise

    # Pull a final summary from the shared state.
    final = _parse_tool_result(
        "agenthpc_get_all_results",
        await project_agent.call_tool(
            "agenthpc_get_all_results",
            {
                "app_type": app_type,
                "target_score": target_score,
                "max_trials": max_trials,
            },
        ),
    )
    summary_msg = (
        f"🏁 Worker pool finished after **{final.get('num_trials', 0)} trials**. "
        f"Best Tc = **{final.get('best_score', 0.0):.1f} K** "
        f"at {final.get('best_parameters')}"
    )
    log("INFO", summary_msg)
    progress(summary_msg)

    # Workers already display the plot after every successful trial. Do
    # one final refresh here as a safety net so the chat UI always shows
    # the very last state — covers the edge case where the final trial's
    # display call lost the race with the pool's exit signal.
    await _show_alloy_plot(project_agent, emit_tool_event, log)

    return {
        "app_type": app_type,
        "num_workers": num_workers,
        "num_trials": final.get("num_trials"),
        "best_parameters": final.get("best_parameters"),
        "best_score": final.get("best_score"),
        "threshold_reached": final.get("threshold_reached"),
        "budget_exhausted": final.get("budget_exhausted"),
        "stopped_by_user": final.get("stopped_by_user"),
        "trials": final.get("trials", []),
        "plot_path": _ALLOY_PLOT_URI,
    }
