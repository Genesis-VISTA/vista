"""
MCP for remote HPC job submission.
"""

from __future__ import annotations
import asyncio
import json
import re
import subprocess
import asyncssh
import textwrap
import time
import json
import dataclasses
from datetime import timedelta
import logging
from pathlib import Path
import shlex
import tenacity
from cachetools import TTLCache
from fastmcp import FastMCP, Context
from .config import settings

@dataclasses.dataclass
class JobInfo:
    slurm_script: Path
    description: str

def get_available_jobs() -> dict[str, JobInfo]:
    jobs = {}
    for file in settings.local_hpc_jobs_dir.iterdir():
        if file.is_dir():
            slurm_scripts = list(file.glob("*.slurm"))
            if len(slurm_scripts) != 1:
                raise ValueError(f"Expected to find one .slurm script in {file}, found {len(slurm_scripts)}")
            slurm_script = slurm_scripts[0]
            readme = file / "README.md"
            if not readme.exists():
                raise ValueError(f"No README.md in {file}")
            description = readme.read_text().strip()
            if not description.startswith(f"# {file.name}"):
                raise ValueError(f'Job README.md should start with "# {file.name}" header')
            jobs[file.name] = JobInfo(
                slurm_script=slurm_script.relative_to(file),
                description=description,
            )
    return jobs


AVAILABLE_JOBS = get_available_jobs()


def build_job_descriptions() -> str:
    return '\n\n\n'.join(info.description for name, info in AVAILABLE_JOBS.items())


MAX_NODES = 64
MAX_TIME = "4:00:00"

def parse_time_limit(s: str):
    """Parse a time delta string in 'h:mm:ss' format."""
    try:
        hours, minutes, seconds = s.split(":")
        return timedelta(hours=int(hours), minutes=int(minutes), seconds=int(seconds))
    except:
        raise ValueError(f"Invalid time limit: {s}")


@tenacity.retry(
    stop = tenacity.stop_after_attempt(4),
    wait = tenacity.wait_random_exponential(multiplier=0.5, max = 10),
    retry = tenacity.retry_if_exception_type(asyncssh.ChannelOpenError),
    reraise = True,
)
async def remote_bash(ssh_conn: asyncssh.SSHClientConnection, command: str, **kwargs) -> str:
    """
    Run a bash command on the remote HPC system
    Retries on ChannelOpenError. Frontier seems to have MaxSessions set to 1, and sometimes fails
    if you run a command too soon after the previous, so retry with delay when that happens.
    """
    kwargs = {
        "check": False,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        **kwargs,
    }
    result = await ssh_conn.run(f"bash -c {shlex.quote(command)}", **kwargs)
    return result.stdout


def get_tool_call_string(tool: str, /, **kwargs):
    kwargs = {k: v for k, v in kwargs.items() if v != None}
    if kwargs:
        return (
            f"{tool}(\n" +
            ',\n'.join(f"  {k}={json.dumps(v)}" for k, v in kwargs.items()) +
            "\n)"
        )
    else:
        return f"{tool}()"


@dataclasses.dataclass
class SSHLoginInfo:
    user: str
    password: str


@dataclasses.dataclass
class Confirmation:
    confrim: bool = False


# FastMCP has a `ctx.set_stat` function but it can only store serializable types, so we'll keep our
# own map of MCP session id to SSHClientConnection. This may cause problems if we scale the MCP up
#to multiple workers. Time out sessions after 1-hour (regardless of if they've been used recently)
_ssh_connections: TTLCache[str, asyncssh.SSHClientConnection] = TTLCache(maxsize=128, ttl=3600)

async def get_ssh_conn(
    ctx: Context, message: str, force_confirmation = False,
) -> asyncssh.SSHClientConnection:
    """
    Elicit for SSH credentials, or use the cached SSH connection.
    Pass force_confirmation if you want to always have a confirmation checkbox even if the ssh
    connection is cached.
    """
    if ctx.session_id not in _ssh_connections:
        logging.info(f"Requesting user login to {settings.hpc_host}")
        max_attempts = 3
        attempt = 1
        conn = None
        while attempt <= max_attempts and not conn:
            retry_note = f" (attempt {attempt}/{max_attempts})" if attempt > 1 else ""
            result = await ctx.elicit(
                message=f"Log in to {settings.hpc_host}{retry_note} to run:\n{message}",
                response_type=SSHLoginInfo
            )

            if result.action != "accept":
                raise Exception("Unable to launch job, user cancelled login")

            try:
                conn = await asyncssh.connect(settings.hpc_host,
                    username = result.data.user,
                    password = result.data.password,
                    login_timeout = 60,
                    connect_timeout = 60,
                )
            except (asyncssh.DisconnectError, asyncssh.PermissionDenied, OSError) as e:
                if attempt >= max_attempts:
                    raise Exception(f"SSH login failed after {max_attempts} attempts: {e}")
                else:
                    logging.warning(f"SSH login attempt {attempt}/{max_attempts} failed: {e}")

            attempt += 1
        _ssh_connections[ctx.session_id] = conn
    elif force_confirmation:
        logging.info(f"Using cached ssh connection to {settings.hpc_host}, with forced confirmation")
        result = await ctx.elicit(
            message=f"Confirm running on {settings.hpc_host}:\n{message}",
            response_type=Confirmation,
        )
        if result.action != "accept" or not result.data.confrim:
            raise Exception("Job submission cancelled by user")
    else:
        logging.info(f"Using cached ssh connection to {settings.hpc_host}")

    return _ssh_connections[ctx.session_id]


mcp = FastMCP("Submit Job")


@mcp.tool(
    description=textwrap.dedent(f"""
        Submit a Slurm job to the HPC system.

        Args:
            job: The name of the job to run (available jobs: {' '.join(AVAILABLE_JOBS.keys())})
            nodes: Number of nodes for the job (max: {MAX_NODES})
            time_limit: Time limit for the job in "h:mm:ss" format (max: {MAX_TIME})
            script_args: Extra arguments to pass to the script

        Returns:
            The slurm job id.

        Available Jobs:

        {textwrap.indent(build_job_descriptions(), '        ').strip()}
    """).strip(),
)
async def submit_hpc_job(
    ctx: Context,
    job: str,
    nodes: int | None = None,
    time_limit: str | None = None,
    script_args: str | None = None,
) -> str:
    if job not in AVAILABLE_JOBS:
        raise ValueError(f"{job} is not recognized, should be one of: {' '.join(AVAILABLE_JOBS)}")
    if nodes and nodes > MAX_NODES:
        raise ValueError(f"To many nodes specified (max {MAX_NODES})")
    if time_limit and parse_time_limit(time_limit) > parse_time_limit(MAX_TIME):
        raise ValueError(f"Time limit to large (max: {MAX_TIME})")

    ssh_conn = await get_ssh_conn(ctx,
        message = get_tool_call_string("submit_hpc_job",
            job = job,
            nodes = nodes,
            time_limit = time_limit,
            script_args = script_args,
        ),
        force_confirmation = True,
    )

    setup_script = settings.remote_hpc_jobs_dir / settings.session_id / 'setup.sh'
    remote_job_dir = settings.remote_hpc_jobs_dir / settings.session_id / job
    check_result = await remote_bash(ssh_conn, f'[ -d {shlex.quote(str(remote_job_dir))} ] && echo true || echo false"')
    if check_result.strip() != "true":
        await remote_bash(ssh_conn, f'mkdir -p {shlex.quote(str(remote_job_dir.parent))}')
        await remote_bash(ssh_conn, f'echo {shlex.quote(settings.remote_hpc_jobs_setup_script)} > {shlex.quote(str(setup_script))}')
        await asyncio.sleep(1)
        await asyncssh.scp(
            str(settings.local_hpc_jobs_dir / job),
            (ssh_conn, str(remote_job_dir)),
            recurse=True,
        )
        logging.info(f"Synced job to {remote_job_dir}")
        # TODO: should clean up the job script eventually, but don't want to do it on close as we may
        # want to leave jobs running between sessions. Probably best would be to periodically delete
        # completed jobs from old sessions in the dir.

    remote_job_script =  remote_job_dir / AVAILABLE_JOBS[job].slurm_script
    args = ["sbatch"]
    args.extend(["--chdir", str(remote_job_script.parent)])
    if nodes:
        args.extend(['-N', str(nodes)])
    if time_limit:
        args.extend(["-t", time_limit])
    args.extend(["--export", f"ALL,VISTA_SETUP_SCRIPT={setup_script},VISTA_OUT={settings.remote_hpc_jobs_dir}/out"])
    args.extend(["-o", f"{settings.remote_hpc_jobs_dir}/out/%j/log.out"])
    args.extend(["-J", f"vista-{job}"])
    args.append(str(remote_job_script))
    if script_args:
        args.extend(shlex.split(script_args))

    result = await remote_bash(ssh_conn, shlex.join(args))

    match = re.search(r"submitted batch job (\d+)", result.lower())
    if match:
        result = match[1]
        logging.info(f"Submitted job {result} to {settings.hpc_host}")
    else:
        raise ValueError("Job failed to launch: " + result)

    return result


@mcp.tool()
async def get_hpc_job_status(ctx: Context, job_id: str) -> str:
    """
    Get the status and logs of a submitted Slurm job.

    Args:
        job_id: The slurm job id

    """
    ssh_conn = await get_ssh_conn(ctx,
        message = get_tool_call_string("get_hpc_job_status", job_id = job_id),
    )
    sacct_out = await remote_bash(ssh_conn, f"sacct --json -j {shlex.quote(job_id)} --user $USER")
    try:
        sacct_jobs = json.loads(sacct_out)["jobs"]
    except:
        raise ValueError("Malformed sacct output")
    if len(sacct_jobs) <= 0 or not sacct_jobs[0]['name'].startswith("vista-"):
        raise ValueError(f"No job {job_id} found")

    state = sacct_jobs[0]['state']['current'][0]
    log_path = f"{settings.remote_hpc_jobs_dir}/out/{job_id}/log.out"
    logs = await remote_bash(ssh_conn, f"cat {shlex.quote(log_path)} 2>/dev/null || true")

    return f"JOB ID: {job_id}\nSTATE: {state}\nLOGS:\n{logs}"


@mcp.tool()
async def list_hpc_jobs(ctx: Context) -> str:
    """
    List all submitted HPC jobs.
    """
    ssh_conn = await get_ssh_conn(ctx,
        message = get_tool_call_string("list_hpc_jobs"),
    )
    sacct_out = await remote_bash(ssh_conn, "sacct --json --allocations --user $USER")
    try:
        sacct_jobs = json.loads(sacct_out)["jobs"]
    except Exception:
        raise ValueError("Malformed sacct output")

    cutoff = time.time() - 24 * 60 * 60

    lines = []
    for job in sacct_jobs:
        if not job["name"].startswith("vista-"):
            continue
        job_id = str(job["job_id"])

        state = job["state"]["current"][0]
        end_time = job.get("time", {}).get("end", 0)
        if end_time and end_time < cutoff:
            continue

        lines.append(f"{job_id} {state}")

    return "\n".join(lines) if lines else "No jobs found."
