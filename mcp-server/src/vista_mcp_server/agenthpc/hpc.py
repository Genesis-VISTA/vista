"""
Remote-HPC operations for the agenthpc sub-MCP.

Every function here runs via an open SSH connection — no local subprocess
calls. The run directory and all input files already live on the remote
cluster under ``app_config["work_dir"]``; Vista only orchestrates: create a
per-trial subdirectory, write the composition file, submit the Slurm job,
poll ``squeue``, and read the log.
"""

from __future__ import annotations

import re
import shlex

import asyncssh

from ..lib.ssh import remote_bash


async def submit_monbtaw(
    ssh_conn: asyncssh.SSHClientConnection,
    app_config: dict,
    parameters: tuple[float, float, float, float],
    trial_number: int,
) -> str:
    """
    Prepare a MoNbTaW run directory on the remote host and submit it with sbatch.
    Returns the slurm job id.
    """
    e1, e2, e3, e4 = parameters
    work_dir = app_config["work_dir"]
    run_subdir = f"run_{trial_number}"
    run_dir = f"{work_dir}/{run_subdir}"
    sbatch_script = app_config["sbatch_script"]
    composition_file = app_config["composition_file"]
    # Glob patterns must remain unquoted so the remote shell expands them.
    # work_dir is quoted and we cd into it first so only the patterns are globbed.
    patterns_expr = " ".join(app_config["file_patterns"])

    prep_cmd = (
        f"set -e; "
        f"cd {shlex.quote(work_dir)}; "
        f"mkdir -p {shlex.quote(run_subdir)}; "
        f"cp {patterns_expr} {shlex.quote(run_subdir)}/; "
        f"printf '%s %s %s %s' "
        f"{shlex.quote(f'{e1}')} {shlex.quote(f'{e2}')} "
        f"{shlex.quote(f'{e3}')} {shlex.quote(f'{e4}')} "
        f"> {shlex.quote(f'{run_subdir}/{composition_file}')}"
    )
    prep_out = await remote_bash(ssh_conn, prep_cmd)
    # `set -e` means non-zero exit would come back as non-empty stderr captured on stdout.
    if prep_out.strip():
        raise RuntimeError(f"Failed to prepare MoNbTaW run dir: {prep_out.strip()}")

    sbatch_cmd = (
        f"sbatch --chdir={shlex.quote(run_dir)} "
        f"{shlex.quote(f'{run_dir}/{sbatch_script}')}"
    )
    sbatch_out = await remote_bash(ssh_conn, sbatch_cmd)
    match = re.search(r"Submitted batch job (\d+)", sbatch_out)
    if not match:
        raise RuntimeError(f"sbatch did not return a job id: {sbatch_out.strip()}")
    return match.group(1)


async def job_in_queue(
    ssh_conn: asyncssh.SSHClientConnection,
    job_id: str,
) -> bool:
    """True iff the job is still present in squeue."""
    out = await remote_bash(
        ssh_conn,
        f"squeue --noheader --job {shlex.quote(job_id)} 2>/dev/null || true",
    )
    return bool(out.strip())


async def log_exists(
    ssh_conn: asyncssh.SSHClientConnection,
    log_path: str,
) -> bool:
    out = await remote_bash(
        ssh_conn,
        f"test -f {shlex.quote(log_path)} && echo yes || echo no",
    )
    return out.strip() == "yes"


async def read_log(
    ssh_conn: asyncssh.SSHClientConnection,
    log_path: str,
) -> str:
    return await remote_bash(ssh_conn, f"cat {shlex.quote(log_path)}")


async def scancel_job(
    ssh_conn: asyncssh.SSHClientConnection,
    job_id: str,
) -> str:
    """
    Cancel a Slurm job by id. Returns scancel's stdout/stderr (usually empty
    on success). Callers should treat a non-fatal response as "OK, moved on"
    — scancel is idempotent; jobs already gone simply produce no output.
    """
    return await remote_bash(
        ssh_conn,
        f"scancel {shlex.quote(job_id)} 2>&1 || true",
    )


def monbtaw_log_path(app_config: dict, trial_number: int) -> str:
    return f"{app_config['work_dir']}/run_{trial_number}/{app_config['log_file_name']}"
