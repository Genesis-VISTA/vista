"""
Concurrency tests for agenthpc.mcp.agenthpc_submit_parameter_set.

The new claim-sentinel + per-session lock is the contract that lets the
backend worker pool run N concurrent submissions safely. These tests pin
down its three load-bearing properties without spinning up SSH or HPC:

  1. Two concurrent submits of the SAME composition: one wins, one returns
     ``claim_lost``. Trial counter advances by exactly one.
  2. N concurrent submits of N distinct compositions: all win, all get
     distinct ``trial_number``s, and the trial counter advances by N.
  3. A submit that fails after the SSH/sbatch hop rolls the sentinel back
     so the composition can be retried by another worker.
"""
import asyncio
from typing import Any

import pytest

from vista_mcp_server.agenthpc import mcp as agenthpc_mcp


class FakeContext:
    """Minimal stand-in for fastmcp.Context.

    The tool function only touches ``session_id`` and ``info(message)``;
    everything else FastMCP would otherwise wire up is unused on the claim
    path."""

    def __init__(self, session_id: str = "test-session") -> None:
        self.session_id = session_id

    async def info(self, message: str) -> None:
        return None


@pytest.fixture(autouse=True)
def reset_caches():
    """Each test gets a fresh in-process state — TTLCaches are module
    globals, so we clear them at the boundaries to avoid bleed-through."""
    agenthpc_mcp._results_cache.clear()
    agenthpc_mcp._pending_jobs.clear()
    agenthpc_mcp._session_locks.clear()
    agenthpc_mcp._session_next_trial.clear()
    agenthpc_mcp._session_stop_flags.clear()
    yield
    agenthpc_mcp._results_cache.clear()
    agenthpc_mcp._pending_jobs.clear()
    agenthpc_mcp._session_locks.clear()
    agenthpc_mcp._session_next_trial.clear()
    agenthpc_mcp._session_stop_flags.clear()


@pytest.fixture
def patch_ssh_and_sbatch(monkeypatch):
    """Replace SSH connection + sbatch with deterministic fakes so the
    submit tool exercises only the lock / sentinel / counter logic.

    Returns a list the test can inspect for the order of accepted submits.
    """
    submitted: list[tuple[tuple[float, ...], int]] = []

    async def _fake_ssh(ctx, message, host):
        # A tiny await so concurrent submits can interleave at the natural
        # await points in agenthpc_submit_parameter_set.
        await asyncio.sleep(0)
        return object()

    async def _fake_submit_monbtaw(ssh_conn, app_config, parameters, trial_number):
        submitted.append((tuple(parameters), trial_number))
        return f"job-{trial_number}"

    monkeypatch.setattr(
        agenthpc_mcp, "get_ssh_conn_mcp_elicitation", _fake_ssh,
    )
    monkeypatch.setattr(
        agenthpc_mcp.hpc_ops, "submit_monbtaw", _fake_submit_monbtaw,
    )
    return submitted


@pytest.mark.anyio
async def test_concurrent_same_composition_one_winner(patch_ssh_and_sbatch):
    """When two workers race on the SAME composition, exactly one submits
    a job and the other gets ``claim_lost``."""
    ctx = FakeContext()
    params = [0.25, 0.25, 0.25, 0.25]

    results = await asyncio.gather(
        agenthpc_mcp.agenthpc_submit_parameter_set(ctx, "monbtaw", list(params)),
        agenthpc_mcp.agenthpc_submit_parameter_set(ctx, "monbtaw", list(params)),
    )

    wins = [r for r in results if not r.get("claim_lost") and not r.get("cached")]
    losses = [r for r in results if r.get("claim_lost")]
    assert len(wins) == 1, f"expected exactly one winner, got {results}"
    assert len(losses) == 1, f"expected exactly one claim_lost, got {results}"

    # Only one Slurm submit reached the (mocked) HPC.
    assert len(patch_ssh_and_sbatch) == 1
    # Trial counter advanced by exactly one despite two concurrent calls.
    assert agenthpc_mcp._session_next_trial[(ctx.session_id, "monbtaw")] == 1


@pytest.mark.anyio
async def test_concurrent_distinct_compositions_all_win(patch_ssh_and_sbatch):
    """N concurrent submits of N distinct compositions all succeed, with
    distinct trial_numbers and exactly N Slurm submits."""
    ctx = FakeContext()
    # Four distinct compositions, each summing to 1.0.
    compositions = [
        [0.25, 0.25, 0.25, 0.25],
        [0.30, 0.20, 0.30, 0.20],
        [0.40, 0.20, 0.20, 0.20],
        [0.10, 0.30, 0.30, 0.30],
    ]

    results = await asyncio.gather(*[
        agenthpc_mcp.agenthpc_submit_parameter_set(ctx, "monbtaw", list(c))
        for c in compositions
    ])

    # All four won — no claim_lost, no cached.
    for r in results:
        assert not r.get("claim_lost"), r
        assert not r.get("cached"), r
        assert r.get("job_id") is not None

    trial_numbers = [r["log_params"]["trial_number"] for r in results]
    assert sorted(trial_numbers) == [0, 1, 2, 3], trial_numbers
    assert len(patch_ssh_and_sbatch) == 4
    assert agenthpc_mcp._session_next_trial[(ctx.session_id, "monbtaw")] == 4


@pytest.mark.anyio
async def test_submit_failure_rolls_back_sentinel(monkeypatch):
    """If sbatch raises, the claim sentinel must be removed so a later
    worker can try the same composition without hitting a stale claim."""
    ctx = FakeContext()
    params = [0.25, 0.25, 0.25, 0.25]

    async def _fake_ssh(ctx, message, host):
        await asyncio.sleep(0)
        return object()

    call_count = {"n": 0}

    async def _failing_then_succeeding(ssh_conn, app_config, parameters, trial_number):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated sbatch failure")
        return f"job-{trial_number}"

    monkeypatch.setattr(
        agenthpc_mcp, "get_ssh_conn_mcp_elicitation", _fake_ssh,
    )
    monkeypatch.setattr(
        agenthpc_mcp.hpc_ops, "submit_monbtaw", _failing_then_succeeding,
    )

    with pytest.raises(RuntimeError, match="simulated sbatch failure"):
        await agenthpc_mcp.agenthpc_submit_parameter_set(ctx, "monbtaw", list(params))

    # Sentinel was rolled back — the params key must be absent so the
    # second call sees no in-flight claim.
    results = agenthpc_mcp._session_results(ctx.session_id, "monbtaw")
    assert agenthpc_mcp.key_from_params(*params) not in results

    # A retry should now succeed and produce a fresh trial_number (the
    # counter advanced past the failed allocation; reusing trial_number 0
    # would risk reusing a partially-prepared remote run_dir).
    retry = await agenthpc_mcp.agenthpc_submit_parameter_set(
        ctx, "monbtaw", list(params),
    )
    assert retry.get("job_id") == "job-1"
    assert retry["log_params"]["trial_number"] == 1


@pytest.mark.anyio
async def test_get_all_results_skips_in_flight_sentinels(patch_ssh_and_sbatch):
    """agenthpc_get_all_results must not report a claim sentinel as a
    completed trial — sentinels have score=None."""
    ctx = FakeContext()

    await agenthpc_mcp.agenthpc_submit_parameter_set(
        ctx, "monbtaw", [0.25, 0.25, 0.25, 0.25],
    )
    # The submit registered a sentinel but nobody has called get_job_result
    # yet, so the trial is "in flight" from the cache's point of view.
    state = await agenthpc_mcp.agenthpc_get_all_results(ctx, "monbtaw")
    assert state["num_trials"] == 0
    assert state["in_flight"] == 1
    assert state["best_score"] == 0.0
    assert state["should_stop"] is False


@pytest.mark.anyio
async def test_stop_flag_propagates_to_should_stop(patch_ssh_and_sbatch):
    """The cooperative stop flag set by agenthpc_cancel_all_pending must
    flip should_stop on subsequent get_all_results calls so the worker
    pool exits cleanly."""
    ctx = FakeContext()

    # Set the stop flag directly — agenthpc_cancel_all_pending would set it
    # via SSH, which we'd need to mock more fully. The helper is the same
    # one the cancel tool uses.
    agenthpc_mcp._set_stop_flag(ctx.session_id, "monbtaw", True)

    state = await agenthpc_mcp.agenthpc_get_all_results(ctx, "monbtaw")
    assert state["should_stop"] is True
    assert state["stopped_by_user"] is True
