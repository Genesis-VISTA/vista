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

from __future__ import annotations

import asyncio
import logging
from typing import Any

from cachetools import TTLCache
from fastmcp import Context, FastMCP

from ..lib.ssh import get_ssh_conn, get_tool_call_string
from . import hpc as hpc_ops
from .application import BaseApplication, create_application, key_from_params
from .config import available_apps, get_app_config


mcp = FastMCP("AgentHPC")


# Per-(session, app_type) cache of completed-trial scores.
# Structure: { (session_id, app_type): { param_key: {"parameters": [...], "score": float} } }
# TTL matches _ssh_connections (1 hour) so state disappears alongside the SSH session.
_results_cache: TTLCache[tuple[str, str], dict[str, dict]] = TTLCache(maxsize=256, ttl=3600)


def _get_app(app_type: str) -> BaseApplication:
    return create_application(app_type, get_app_config(app_type))


def _session_results(session_id: str, app_type: str) -> dict[str, dict]:
    cache_key = (session_id, app_type)
    if cache_key not in _results_cache:
        _results_cache[cache_key] = {}
    return _results_cache[cache_key]


# --------------------------------------------------------------------------- tools

@mcp.tool()
async def agenthpc_list_applications() -> dict[str, Any]:
    """List every application available for agentic HPC optimization."""
    return {"applications": available_apps()}


@mcp.tool()
async def agenthpc_get_search_space(app_type: str) -> dict[str, Any]:
    """
    Describe the search space, objective, score threshold, and trial budget
    for ``app_type``. Pure — does not touch HPC.
    """
    return _get_app(app_type).get_search_space_info()


@mcp.tool()
async def agenthpc_submit_parameter_set(
    ctx: Context,
    app_type: str,
    parameters: list[float],
    trial_number: int | None = None,
) -> dict[str, Any]:
    """
    Submit one parameter set to the HPC cluster for evaluation.

    For MoNbTaW, ``parameters`` is ``[Mo, Nb, Ta, W]`` (must sum to ~1.0 and each
    be one of the allowed values from ``agenthpc_get_search_space``).
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
        key = key_from_params(*params)
    else:
        raise ValueError(f"Unsupported application: {app_type}")

    results = _session_results(ctx.session_id, app_type)
    if key in results:
        cached = results[key]
        logging.info(f"agenthpc: returning cached score for {app_type} {key}")
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

    ssh_conn = await get_ssh_conn(
        ctx,
        message=get_tool_call_string(
            "agenthpc_submit_parameter_set",
            app_type=app_type,
            parameters=list(params),
            trial_number=trial_number,
        ),
        host=app.host,
        force_confirmation=True,
    )

    if app_type == "monbtaw":
        job_id = await hpc_ops.submit_monbtaw(ssh_conn, app.app_config, params, trial_number)
        log_params = {"trial_number": trial_number}
    else:
        raise ValueError(f"Unsupported application: {app_type}")

    logging.info(f"agenthpc: submitted {app_type} job {job_id} for params {params}")
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


@mcp.tool()
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
    ssh_conn = await get_ssh_conn(
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


@mcp.tool()
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
    ssh_conn = await get_ssh_conn(
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


@mcp.tool()
async def agenthpc_get_job_result(
    ctx: Context,
    app_type: str,
    job_id: str,
    log_params: dict[str, Any],
    parameters: list[float],
) -> dict[str, Any]:
    """
    Read the completed job's log, extract metrics, compute the score, and cache
    it in the per-session results store. Pass ``parameters`` (the ones originally
    submitted) so the cache key matches what ``agenthpc_get_all_results`` reports.

    Returns ``{score, metrics, parameters, threshold_reached}``.
    """
    app = _get_app(app_type)
    ssh_conn = await get_ssh_conn(
        ctx,
        message=get_tool_call_string(
            "agenthpc_get_job_result", app_type=app_type, job_id=job_id,
        ),
        host=app.host,
    )

    log_path = _log_path(app, log_params)
    if not await hpc_ops.log_exists(ssh_conn, log_path):
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
    results = _session_results(ctx.session_id, app_type)
    results[key] = {"parameters": list(params), "score": score}

    return {
        "status": "success",
        "job_id": job_id,
        "parameters": list(params),
        "score": score,
        "metrics": list(metrics) if metrics else None,
        "threshold_reached": score >= app.score_threshold,
    }


@mcp.tool()
async def agenthpc_get_all_results(
    ctx: Context,
    app_type: str,
) -> dict[str, Any]:
    """
    Return every completed trial for ``app_type`` in the current MCP session,
    along with the best score seen so far. Use this to avoid re-suggesting a
    parameter set that has already been evaluated.
    """
    app = _get_app(app_type)
    results = _session_results(ctx.session_id, app_type)
    trials = [
        {"key": k, "parameters": v["parameters"], "score": v["score"]}
        for k, v in results.items()
    ]
    best_score = max((t["score"] for t in trials), default=0.0)
    return {
        "app_type": app_type,
        "num_trials": len(trials),
        "max_trials": app.max_trials,
        "score_threshold": app.score_threshold,
        "best_score": best_score,
        "threshold_reached": best_score >= app.score_threshold,
        "trials": trials,
    }
