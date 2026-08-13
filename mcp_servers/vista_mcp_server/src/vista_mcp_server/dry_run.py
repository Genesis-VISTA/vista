"""
HPC dry-run (evaluation plan M6, experiment flag §2a Group 3).

When `VISTA_MCP_HPC_DRY_RUN=true`, the HPC job tools short-circuit
S3M/IRI/Globus and return recorded synthetic responses: submit returns a
`dry-<hex>` job id immediately, and status reports `PENDING` for
`VISTA_MCP_HPC_QUEUE_DELAY_S` seconds (wall-clock from submission) then
`COMPLETED`. No real cluster contact and no credentials required, so the
loadgen (`backend/scripts/loadgen.py`) can drive deterministic
replay/concurrency campaigns (E1/E6) and sweep queue-wait behavior (E7b:
5 min / 1 h / 24 h) on a laptop.

This is a behavior-changing flag, not a metrics level — the M7 startup
guard refuses it when `VISTA_ENV=prod`.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from .config import settings


@dataclass
class _DryJob:
    cluster: str
    job: str
    nodes: int
    duration_s: int
    submitted_at: float  # time.monotonic()
    queue_delay_s: float
    cancelled: bool = False


# In-process registry of dry-run jobs. Lives alongside submit_job_mcp's
# `_submitted_jobs` (which still records cluster/paths for dispatch); this
# adds the synthetic-state bookkeeping the real path gets from IRI.
_dry_jobs: dict[str, _DryJob] = {}


def enabled() -> bool:
    return settings.hpc_dry_run


def is_dry_job(job_id: str) -> bool:
    return job_id in _dry_jobs


def record_submit(cluster: str, job: str, nodes: int, duration_s: int) -> str:
    """Register a synthetic submission and return its job id. The queue delay
    is snapshotted per job so a single process can host jobs from runs that
    used different delays."""
    job_id = f"dry-{uuid.uuid4().hex[:12]}"
    _dry_jobs[job_id] = _DryJob(
        cluster=cluster,
        job=job,
        nodes=nodes,
        duration_s=duration_s,
        submitted_at=time.monotonic(),
        queue_delay_s=settings.hpc_queue_delay_s,
    )
    return job_id


def state(job_id: str) -> str:
    """Synthetic Slurm-ish state, derived from wall-clock since submission."""
    j = _dry_jobs[job_id]
    if j.cancelled:
        return "CANCELLED"
    elapsed = time.monotonic() - j.submitted_at
    return "PENDING" if elapsed < j.queue_delay_s else "COMPLETED"


def status_text(job_id: str) -> str:
    """Status string matching the real tools' `KEY=value` + logs layout, so
    a caller (agent or loadgen) can't tell the shape apart."""
    j = _dry_jobs[job_id]
    st = state(job_id)
    meta = {"JOB_ID": job_id, "CLUSTER": j.cluster, "STATE": st}
    if st == "COMPLETED":
        meta["EXIT_CODE"] = 0
    header = "\n".join(f"{k}={v}" for k, v in meta.items())
    if st in ("PENDING", "CANCELLED"):
        note = (
            "(dry-run: waiting in synthetic queue; logs appear once it runs)"
            if st == "PENDING"
            else "(dry-run: job cancelled)"
        )
        return f"{header}\n\n{note}"
    return (
        f"{header}\n\n"
        "--- LOGS ---\n(dry-run synthetic log; no real cluster output)\n"
        "--- OUTPUT FILES ---\n(none)"
    )


def cancel(job_id: str) -> str:
    _dry_jobs[job_id].cancelled = True
    return f"Cancellation requested for dry-run job {job_id}."


def reset() -> None:
    """Clear the registry (tests)."""
    _dry_jobs.clear()
