"""
FastMCP sub-server for agentic HPC optimization.

Vista's chat agent is the driver: it reads the search space, proposes a
parameter set, calls ``agenthpc_submit_parameter_set``, waits for the job,
reads the score, and iterates. State is kept *minimal* and per-session:
live SSH connections live in ``lib.ssh._ssh_connections``; completed-trial
scores live in this module's ``_results_cache``. No cross-session state.

Every tool accepts an ``app_type`` argument and resolves the target HPC host
from ``applications.yaml`` — so MoNbTaW (Andes) and future apps (Frontier,
etc.) cohabit without a global "current application" handshake.
"""
import asyncio
import logging
from pathlib import Path
from typing import Any

from cachetools import TTLCache
from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from ..config import settings
from ..lib.ssh import get_ssh_conn_mcp_elicitation
from ..lib.misc import get_tool_call_string
from . import hpc as hpc_ops
from .application import BaseApplication, create_application, key_from_params
from .config import available_apps, get_app_config


mcp = FastMCP("AgentHPC")


# Per-(session, app_type) cache of completed-trial scores.
# Structure: { (session_id, app_type): { param_key: {"parameters": [...], "score": float} } }
# TTL matches _ssh_connections (1 hour) so state disappears alongside the SSH session.
_results_cache: TTLCache[tuple[str, str], dict[str, dict]] = TTLCache(maxsize=256, ttl=3600)


# Per-(session, app_type) registry of in-flight Slurm jobs — entries are added
# on submit and removed either by agenthpc_get_job_result on success or by one
# of the cancel tools. Lets the "cancel the optimization" UX find every job the
# current session has launched without requiring the LLM to remember job ids.
# Structure: { (session_id, app_type): { job_id: {"parameters": [...],
#                                                  "trial_number": int | None,
#                                                  "log_params": dict } } }
_pending_jobs: TTLCache[tuple[str, str], dict[str, dict]] = TTLCache(maxsize=256, ttl=3600)


def _get_app(app_type: str) -> BaseApplication:
    return create_application(app_type, get_app_config(app_type))


def _effective_threshold(app: BaseApplication, target_score: float | None) -> float:
    return float(target_score) if target_score is not None else app.score_threshold


def _effective_max_trials(app: BaseApplication, max_trials: int | None) -> int:
    return int(max_trials) if max_trials is not None else app.max_trials


def _session_results(session_id: str, app_type: str) -> dict[str, dict]:
    cache_key = (session_id, app_type)
    if cache_key not in _results_cache:
        _results_cache[cache_key] = {}
    return _results_cache[cache_key]


def _session_pending(session_id: str, app_type: str) -> dict[str, dict]:
    cache_key = (session_id, app_type)
    if cache_key not in _pending_jobs:
        _pending_jobs[cache_key] = {}
    return _pending_jobs[cache_key]


# --------------------------------------------------------------------------- tools

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
async def agenthpc_list_applications() -> dict[str, Any]:
    """List every application available for agentic HPC optimization."""
    return {"applications": available_apps()}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
async def agenthpc_get_search_space(
    app_type: str,
    target_score: float | None = None,
    max_trials: int | None = None,
) -> dict[str, Any]:
    """
    Describe the search space, objective, score threshold, and trial budget
    for ``app_type``. Pure — does not touch HPC.

    Pass ``target_score`` and/or ``max_trials`` to override the YAML defaults
    (e.g. with values the user requested at the start of the optimization).
    The returned ``score_threshold`` and ``max_trials`` fields will reflect
    the effective values so you can use them as stopping criteria.
    """
    app = _get_app(app_type)
    info = app.get_search_space_info()
    info["score_threshold"] = _effective_threshold(app, target_score)
    info["max_trials"] = _effective_max_trials(app, max_trials)
    info["defaults"] = {
        "score_threshold": app.score_threshold,
        "max_trials": app.max_trials,
    }
    return info


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True))
async def agenthpc_submit_parameter_set(
    ctx: Context,
    app_type: str,
    parameters: list[float],
    trial_number: int | None = None,
) -> dict[str, Any]:
    """
    Submit one parameter set to the HPC cluster for evaluation.

    For MoNbTaW, ``parameters`` is ``[Mo, Nb, Ta, W]`` — the four atom-fraction
    percentages. They MUST sum to exactly 1.0 (tolerance 1e-3); any other sum
    is rejected with an error before anything is submitted to HPC.
    ``trial_number`` controls the remote run directory name; if omitted, the
    server uses the count of already-submitted trials in this session.

    Returns ``{job_id, parameters, log_params, cached?}``. Pass ``job_id`` and
    ``log_params`` back to the status/result tools. If the exact parameter set
    has already been evaluated in this session, returns ``{"cached": true,
    "score": ...}`` instead of submitting a new job.
    """
    app = _get_app(app_type)

    if app_type == "monbtaw":
        if len(parameters) != 4:
            raise ValueError(f"MoNbTaW expects 4 parameters, got {len(parameters)}")
        params = tuple(float(p) for p in parameters)
        total = sum(params)
        # MoNbTaW parameters are atom-fraction percentages — they MUST sum to
        # 1.0 or the underlying simulation input is physically meaningless.
        # Reject malformed proposals server-side with a precise error so the
        # LLM can self-correct on the next turn.
        if abs(total - 1.0) > 1e-3:
            raise ValueError(
                f"MoNbTaW composition must sum to 1.0; got "
                f"[Mo={params[0]}, Nb={params[1]}, Ta={params[2]}, W={params[3]}] "
                f"which sums to {total:.6f} (off by {total - 1.0:+.6f}). "
                f"Rescale so the four fractions add up to exactly 1.0 and retry."
            )
        key = key_from_params(*params)
    else:
        raise ValueError(f"Unsupported application: {app_type}")

    results = _session_results(ctx.session_id, app_type)
    if key in results:
        cached = results[key]
        await ctx.info(f"agenthpc: returning cached score for {app_type} {key}")
        return {
            "cached": True,
            "app_type": app_type,
            "parameters": cached["parameters"],
            "score": cached["score"],
        }

    if trial_number is None:
        # Count both completed and in-flight submissions in this session.
        trial_number = sum(1 for _ in results)  # completed so far; collisions
        # are tolerated since trial_number only names the remote run dir.

    ssh_conn = await get_ssh_conn_mcp_elicitation(
        ctx,
        message=get_tool_call_string(
            "agenthpc_submit_parameter_set",
            app_type=app_type,
            parameters=list(params),
            trial_number=trial_number,
        ),
        host=app.host,
    )
    if app_type == "monbtaw":
        job_id = await hpc_ops.submit_monbtaw(ssh_conn, app.app_config, params, trial_number)
        log_params = {"trial_number": trial_number}
    else:
        raise ValueError(f"Unsupported application: {app_type}")

    pending = _session_pending(ctx.session_id, app_type)
    pending[job_id] = {
        "parameters": list(params),
        "trial_number": trial_number,
        "log_params": log_params,
    }

    await ctx.info(f"agenthpc: submitted {app_type} job {job_id} for params {params}")
    return {
        "cached": False,
        "app_type": app_type,
        "job_id": job_id,
        "parameters": list(params),
        "log_params": log_params,
    }


def _log_path(app: BaseApplication, log_params: dict) -> str:
    if app.app_type == "monbtaw":
        return hpc_ops.monbtaw_log_path(app.app_config, int(log_params["trial_number"]))
    raise ValueError(f"Unsupported application: {app.app_type}")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
async def agenthpc_check_job_status(
    ctx: Context,
    app_type: str,
    job_id: str,
    log_params: dict[str, Any],
) -> dict[str, Any]:
    """
    Return the current state of a submitted job:

    - ``running`` — still in the scheduler queue.
    - ``completed`` — left the queue and the expected log file exists.
    - ``no_log`` — left the queue but the log file is missing (likely crashed).

    Pass ``log_params`` exactly as returned by ``agenthpc_submit_parameter_set``.
    """
    app = _get_app(app_type)
    ssh_conn = await get_ssh_conn_mcp_elicitation(
        ctx,
        message=get_tool_call_string(
            "agenthpc_check_job_status", app_type=app_type, job_id=job_id,
        ),
        host=app.host,
    )
    if await hpc_ops.job_in_queue(ssh_conn, job_id):
        return {"status": "running", "job_id": job_id}

    if await hpc_ops.log_exists(ssh_conn, _log_path(app, log_params)):
        return {"status": "completed", "job_id": job_id}

    return {"status": "no_log", "job_id": job_id}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
async def agenthpc_wait_for_job(
    ctx: Context,
    app_type: str,
    job_id: str,
    log_params: dict[str, Any],
    timeout_s: int = 1800,
    poll_interval_s: int = 30,
) -> dict[str, Any]:
    """
    Block on the server until the given job leaves ``completed`` or ``no_log`` state,
    or ``timeout_s`` elapses. Returns the same shape as ``agenthpc_check_job_status``,
    with an extra ``timed_out`` field when the timeout is reached.

    Use this instead of calling ``agenthpc_check_job_status`` in a loop — it avoids
    burning LLM tokens on repeated polling.
    """
    app = _get_app(app_type)
    ssh_conn = await get_ssh_conn_mcp_elicitation(
        ctx,
        message=get_tool_call_string(
            "agenthpc_wait_for_job",
            app_type=app_type,
            job_id=job_id,
            timeout_s=timeout_s,
        ),
        host=app.host,
    )
    log_path = _log_path(app, log_params)
    deadline = asyncio.get_event_loop().time() + max(1, timeout_s)
    while True:
        if not await hpc_ops.job_in_queue(ssh_conn, job_id):
            if await hpc_ops.log_exists(ssh_conn, log_path):
                return {"status": "completed", "job_id": job_id}
            return {"status": "no_log", "job_id": job_id}

        now = asyncio.get_event_loop().time()
        if now >= deadline:
            return {"status": "running", "job_id": job_id, "timed_out": True}

        await asyncio.sleep(min(poll_interval_s, deadline - now))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
async def agenthpc_get_job_result(
    ctx: Context,
    app_type: str,
    job_id: str,
    log_params: dict[str, Any],
    parameters: list[float],
    target_score: float | None = None,
) -> dict[str, Any]:
    """
    Read the completed job's log, extract metrics, compute the score, and cache
    it in the per-session results store. Pass ``parameters`` (the ones originally
    submitted) so the cache key matches what ``agenthpc_get_all_results`` reports.
    Pass ``target_score`` to evaluate ``threshold_reached`` against the user's
    goal instead of the YAML default.

    Returns ``{score, metrics, parameters, threshold_reached}``.
    """
    app = _get_app(app_type)
    ssh_conn = await get_ssh_conn_mcp_elicitation(
        ctx,
        message=get_tool_call_string(
            "agenthpc_get_job_result", app_type=app_type, job_id=job_id,
        ),
        host=app.host,
    )
    log_path = _log_path(app, log_params)
    pending = _session_pending(ctx.session_id, app_type)
    if not await hpc_ops.log_exists(ssh_conn, log_path):
        # Job is gone and no log was produced — drop it from the pending
        # registry so a later "cancel everything" call does not try to
        # scancel a dead job id.
        pending.pop(job_id, None)
        return {
            "status": "no_log",
            "job_id": job_id,
            "message": f"Log file {log_path} does not exist on {app.host}",
        }

    contents = await hpc_ops.read_log(ssh_conn, log_path)
    metrics = app.parse_log(contents)
    score = app.calculate_score(metrics)

    params = tuple(float(p) for p in parameters)
    key = key_from_params(*params)
    trial_number = log_params.get("trial_number") if isinstance(log_params, dict) else None
    results = _session_results(ctx.session_id, app_type)
    # Stash the raw log contents and the trial number so agenthpc_plot_progress
    # can build a cumulative specific-heat-curves plot without re-fetching
    # stat0.dat over SSH for every call.
    results[key] = {
        "parameters": list(params),
        "score": score,
        "trial_number": int(trial_number) if trial_number is not None else None,
        "log_contents": contents,
    }
    pending.pop(job_id, None)

    threshold = _effective_threshold(app, target_score)
    return {
        "status": "success",
        "job_id": job_id,
        "parameters": list(params),
        "score": score,
        "metrics": list(metrics) if metrics else None,
        "score_threshold": threshold,
        "threshold_reached": score >= threshold,
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
async def agenthpc_get_all_results(
    ctx: Context,
    app_type: str,
    target_score: float | None = None,
    max_trials: int | None = None,
) -> dict[str, Any]:
    """
    Return every completed trial for ``app_type`` in the current MCP session,
    along with the best score seen so far. Use this to avoid re-suggesting a
    parameter set that has already been evaluated, and as the stopping-criterion
    check at the top of each optimization-loop iteration.

    Pass ``target_score`` and/or ``max_trials`` to override the YAML defaults.
    ``threshold_reached`` and ``budget_exhausted`` in the response reflect the
    effective values so you can stop the loop when either is true.
    """
    app = _get_app(app_type)
    results = _session_results(ctx.session_id, app_type)
    trials = [
        {"key": k, "parameters": v["parameters"], "score": v["score"]}
        for k, v in results.items()
    ]
    best_score = max((t["score"] for t in trials), default=0.0)
    best_trial = max(trials, key=lambda t: t["score"]) if trials else None

    threshold = _effective_threshold(app, target_score)
    budget = _effective_max_trials(app, max_trials)

    return {
        "app_type": app_type,
        "num_trials": len(trials),
        "max_trials": budget,
        "score_threshold": threshold,
        "best_score": best_score,
        "best_parameters": best_trial["parameters"] if best_trial else None,
        "threshold_reached": best_score >= threshold,
        "budget_exhausted": len(trials) >= budget,
        "should_stop": (best_score >= threshold) or (len(trials) >= budget),
        "trials": trials,
        "defaults": {
            "score_threshold": app.score_threshold,
            "max_trials": app.max_trials,
        },
    }


# --------------------------------------------------------------------------- cancellation

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
async def agenthpc_list_pending_jobs(
    ctx: Context,
    app_type: str,
) -> dict[str, Any]:
    """
    Return the Slurm job ids this MCP session has submitted for ``app_type``
    that have not yet produced a score. Use this before cancelling so you
    can tell the user which jobs are about to go away.
    """
    pending = _session_pending(ctx.session_id, app_type)
    return {
        "app_type": app_type,
        "num_pending": len(pending),
        "jobs": [
            {
                "job_id": job_id,
                "parameters": info["parameters"],
                "trial_number": info.get("trial_number"),
            }
            for job_id, info in pending.items()
        ],
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True))
async def agenthpc_cancel_job(
    ctx: Context,
    app_type: str,
    job_id: str,
) -> dict[str, Any]:
    """
    Cancel a single in-flight Slurm job submitted through this MCP session.
    Idempotent — cancelling a job that has already finished or been cancelled
    is a no-op. Drops the job from the pending-jobs registry.
    """
    app = _get_app(app_type)
    ssh_conn = await get_ssh_conn_mcp_elicitation(
        ctx,
        message=get_tool_call_string(
            "agenthpc_cancel_job", app_type=app_type, job_id=job_id,
        ),
        host=app.host,
    )
    output = await hpc_ops.scancel_job(ssh_conn, job_id)

    pending = _session_pending(ctx.session_id, app_type)
    removed = pending.pop(job_id, None)

    await ctx.info(f"agenthpc: cancelled {app_type} job {job_id}")
    return {
        "status": "cancelled",
        "job_id": job_id,
        "was_tracked": removed is not None,
        "parameters": removed["parameters"] if removed else None,
        "scancel_output": output.strip() or None,
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True))
async def agenthpc_cancel_all_pending(
    ctx: Context,
    app_type: str,
) -> dict[str, Any]:
    """
    Cancel every in-flight job submitted through this MCP session for
    ``app_type``. Use when the user asks to stop / abort / cancel the
    optimization. Returns the list of cancelled job ids.

    Does not touch completed trials in the results cache — those are kept so
    the user can still see what was found before stopping.
    """
    app = _get_app(app_type)
    pending = _session_pending(ctx.session_id, app_type)
    if not pending:
        return {
            "app_type": app_type,
            "num_cancelled": 0,
            "cancelled": [],
            "message": "No in-flight jobs for this session.",
        }

    ssh_conn = await get_ssh_conn_mcp_elicitation(
        ctx,
        message=get_tool_call_string(
            "agenthpc_cancel_all_pending", app_type=app_type,
        ),
        host=app.host,
    )
    cancelled: list[dict[str, Any]] = []
    # Snapshot keys so we can mutate the dict while iterating.
    for job_id in list(pending.keys()):
        info = pending.pop(job_id, None) or {}
        output = await hpc_ops.scancel_job(ssh_conn, job_id)
        cancelled.append({
            "job_id": job_id,
            "parameters": info.get("parameters"),
            "trial_number": info.get("trial_number"),
            "scancel_output": output.strip() or None,
        })

    await ctx.info(f"agenthpc: cancelled {len(cancelled)} pending {app_type} jobs")
    return {
        "app_type": app_type,
        "num_cancelled": len(cancelled),
        "cancelled": cancelled,
    }


# --------------------------------------------------------------------------- visualization

# Sandbox-side prefix used when advertising the plot path to the chat UI.
# display_file_mcp resolves this back to settings.output_dir via settings.volumes.
_PLOT_SANDBOX_DIR = "/mnt/data/output/alloy-progress"


def _parse_stat0(contents: str) -> tuple[list[float], list[float]]:
    """Return ``(temperatures, specific_heats)`` parsed from stat0.dat-style
    whitespace-separated text. Malformed or header lines are skipped."""
    temps: list[float] = []
    cvs: list[float] = []
    for line in contents.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        try:
            temps.append(float(parts[0]))
            cvs.append(float(parts[2]))
        except (ValueError, IndexError):
            continue
    return temps, cvs


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
async def agenthpc_plot_progress(
    ctx: Context,
    app_type: str,
) -> str:
    """
    Render specific-heat-vs-temperature curves for every completed trial in
    this MCP session on a single figure, labelled by trial number, and save
    the PNG to the output panel. Call this after each successful
    ``agenthpc_get_job_result`` so the user can watch the campaign evolve.

    Uses the ``stat0.dat`` contents cached by ``agenthpc_get_job_result`` —
    does not re-open the SSH connection. If no trials have completed yet,
    returns a short message instead of a plot.
    """
    results = _session_results(ctx.session_id, app_type)
    if not results:
        return "No completed trials yet — nothing to plot."

    # Matplotlib must be imported lazily and with Agg because this process is
    # a long-running MCP server, not a one-shot sandbox job.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Order trials by trial_number so colors follow the campaign timeline.
    trials = sorted(
        (r for r in results.values() if r.get("log_contents")),
        key=lambda r: (r.get("trial_number") is None, r.get("trial_number") or 0),
    )
    if not trials:
        return "No completed trials have cached log contents — nothing to plot."

    n = len(trials)
    # viridis reads monotonically — aligns with "later trial = different shade",
    # making temporal progression visible at a glance.
    cmap = matplotlib.colormaps["viridis"].resampled(max(n, 2))

    fig, ax = plt.subplots(figsize=(8.2, 5.0), dpi=140)
    skipped: list[int] = []
    plotted = 0
    best_score = -float("inf")
    best_label = None

    for i, trial in enumerate(trials):
        temps, cvs = _parse_stat0(trial["log_contents"])
        if not temps:
            skipped.append(trial.get("trial_number") or -1)
            continue
        params = trial.get("parameters", [])
        tn = trial.get("trial_number")
        # Compact legend entry: trial tag + composition + Tc (score).
        comp = ", ".join(f"{p:.2f}" for p in params) if params else ""
        score = trial.get("score", 0.0)
        label = f"Trial {tn if tn is not None else i+1}: ({comp}) → Tc={score:.1f}"
        ax.plot(temps, cvs, color=cmap(i), linewidth=1.5, label=label)
        plotted += 1
        if score > best_score:
            best_score = score
            best_label = label

    ax.set_xlabel("Temperature (K)", fontsize=11)
    ax.set_ylabel("Specific heat  C$_v$", fontsize=11)
    ax.set_title(
        f"MoNbTaW progress — {plotted} trial{'s' if plotted != 1 else ''}"
        + (f" · best Tc = {best_score:.1f} K" if best_label else ""),
        fontsize=12, pad=12,
    )
    ax.grid(True, alpha=0.25, linestyle="--", linewidth=0.6)
    # Legend outside the plot area when there are many curves.
    legend_kwargs = {"fontsize": 8, "frameon": False}
    if plotted > 6:
        ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), **legend_kwargs)
        fig.subplots_adjust(right=0.68)
    else:
        ax.legend(loc="best", **legend_kwargs)

    host_dir: Path = settings.output_dir / "alloy-progress"
    host_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{app_type}_progress.png"
    host_path = host_dir / filename
    fig.savefig(host_path, bbox_inches="tight")
    plt.close(fig)

    sandbox_path = f"{_PLOT_SANDBOX_DIR}/{filename}"
    lines = [
        f"Plotted {plotted} trial{'s' if plotted != 1 else ''} for {app_type}.",
    ]
    if best_label:
        lines.append(f"Best so far: {best_label}")
    if skipped:
        lines.append(f"Skipped trials (no parseable stat0.dat): {skipped}")
    # The chat route matches /Plot saved to\s+(\/\S+\.(?:png|jpg|jpeg|svg|gif))/
    # to auto-display the figure — keep this line verbatim.
    lines.append(f"Plot saved to {sandbox_path}")
    await ctx.info(f"agenthpc: rendered progress plot → {host_path} (plotted={plotted})")
    return "\n".join(lines)
