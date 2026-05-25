"""
MCP for remote HPC job submission. Dispatches between:
- Odo (OLCF) via the S3M API (`lib/s3m.py`)
- Perlmutter (NERSC) via the IRI API and amscrot SDK (`lib/iri.py`)
"""

from __future__ import annotations
import itertools, json, logging, os, shlex, textwrap, dataclasses
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastmcp import FastMCP, Context
from fastmcp.exceptions import ToolError
from pydantic import BaseModel
from mcp.types import ToolAnnotations

from .config import settings
from .lib import s3m
from .lib.ssh import Confirmation
from .lib.s3m import S3mDefaults, get_s3m_client, init_s3m_ssh_conn
from .lib.iri import IriClient, IriDefaults, create_iri_client
from .lib.user_config import (
    UserConfig,
    get_user_config,
    require_nersc_iri_token,
    require_remote_hpc_jobs_dir,
    require_s3m_token,
)
from .lib.misc import parse_time_limit, validate_job_id, get_tool_call_string


Cluster = Literal["odo", "perlmutter", "frontier"]
""" Supported HPC clusters. """

PERLMUTTER_JOB_SCRIPT = "job.perlmutter.slurm"
PERLMUTTER_SETUP_SCRIPT = "setup_perlmutter.sh"
""" Optional pre_launch setup script in each job dir; inlined into JobSpec.attributes.pre_launch. """

PERLMUTTER_UPLOAD_SKIP = {
    "README.md",
    "cluster_defaults.json",
    "s3m_defaults.json",      # legacy from before the rename
    "job.slurm",              # Odo-specific
    PERLMUTTER_JOB_SCRIPT,    # inlined into the IRI JobSpec
    PERLMUTTER_SETUP_SCRIPT,  # inlined as pre_launch
}
""" Files in `hpc_jobs/<job>/` that the Perlmutter dispatcher does NOT upload to RUN_DIR_Perlmutter. """


class ClusterDefaults(BaseModel):
    """
    Per-job defaults loaded from `<job>/cluster_defaults.json`. A job opts in to a cluster
    by including the corresponding section.
    """
    odo: S3mDefaults | None = None
    perlmutter: IriDefaults | None = None
    frontier: S3mDefaults | None = None


@dataclasses.dataclass
class JobInfo:
    name: str
    description: str
    cluster_defaults: ClusterDefaults


def get_available_jobs() -> dict[str, JobInfo]:
    jobs = {}
    for file in settings.local_hpc_jobs_dir.iterdir():
        if file.is_dir():
            job_script = file / "job.slurm"
            if not job_script.exists():
                raise ValueError(f"Job {file} missing job.slurm")
            readme = file / "README.md"
            if not readme.exists():
                raise ValueError(f"No README.md in {file}")
            description = readme.read_text().strip()
            if not description.startswith(f"# {file.name}"):
                raise ValueError(f'Job README.md should start with "# {file.name}" header')
            cluster_defaults_file = file / "cluster_defaults.json"
            if cluster_defaults_file.exists():
                cluster_defaults = ClusterDefaults.model_validate_json(cluster_defaults_file.read_text())
            else:
                cluster_defaults = ClusterDefaults()
            jobs[file.name] = JobInfo(
                name=file.name,
                description=description,
                cluster_defaults=cluster_defaults,
            )
    return jobs


AVAILABLE_JOBS = get_available_jobs()


def build_job_descriptions() -> str:
    return '\n\n\n'.join(info.description for name, info in AVAILABLE_JOBS.items())


MAX_NODES = 64
MAX_TIME = int(parse_time_limit("4:00:00").total_seconds())


@asynccontextmanager
async def lifespan(server):
    # TODO Temporary workaround for Odo s3m API limitations, we use ssh for file operations.
    await init_s3m_ssh_conn()
    try:
        yield
    finally:
        if s3m._ssh_conn:
            s3m._ssh_conn.close()
            s3m._ssh_conn = None


mcp = FastMCP("Submit Job", lifespan=lifespan)


@dataclasses.dataclass
class SubmittedJob:
    cluster: Cluster
    # Perlmutter-only: rendered absolute paths after %j substitution.
    log_path: str | None = None
    output_dir: str | None = None


# In-memory cache of job_id -> SubmittedJob for jobs submitted in this session. Used by
# get_hpc_job_status / get_hpc_job_outputs / list_hpc_jobs to dispatch when the caller
# doesn't pass an explicit `cluster` arg, and to resolve Perlmutter log/output paths.
_submitted_jobs: dict[str, SubmittedJob] = {}


def _default_cluster(cfg: UserConfig) -> Cluster:
    """ Return the only configured cluster. Raises if zero or both are configured. """
    has_odo = bool(cfg.s3m_token)
    has_nersc = bool(cfg.nersc_iri_token)
    if has_odo and not has_nersc:
        return "odo"
    if has_nersc and not has_odo:
        return "perlmutter"
    if has_odo and has_nersc:
        raise ToolError(
            "Both Odo and Perlmutter are configured; please pass cluster=\"odo\" or cluster=\"perlmutter\""
        )
    raise ToolError(
        "No HPC cluster configured for this user. Add an S3M token or NERSC IRI "
        "token in the Vista user settings page."
    )


def _resolve_cluster(cluster: Cluster | None, cfg: UserConfig, job_id: str | None = None) -> Cluster:
    """
    Pick a cluster for a tool call. Priority: explicit arg > job_id cache > sole-configured cluster.
    """
    if cluster is not None:
        return cluster
    if job_id is not None and job_id in _submitted_jobs:
        return _submitted_jobs[job_id].cluster
    return _default_cluster(cfg)


def _render_hpc_setup_script(cfg: UserConfig) -> str:
    """ Format the setup script template with both global settings and per-user config. """
    return settings.hpc_setup_script_template.format(
        **settings.model_dump(include={
            "session_id", "hpc_account",
            "s3m_url", "s3m_resource",
            "nersc_iri_url", "nersc_machine",
        }),
        remote_hpc_jobs_dir=require_remote_hpc_jobs_dir(cfg),
    )


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True),
    description=textwrap.dedent(f"""
        Submit a job to the HPC system.

        Args:
            job: The name of the job to run (available jobs: {' '.join(AVAILABLE_JOBS.keys())})
            cluster: Which cluster to submit to ("odo" or "perlmutter"). If only one cluster is
                configured, this can be omitted.
            node_count: Number of nodes for the job (max: {MAX_NODES})
            duration: Time limit for the job in "h:mm:ss" format (max: {MAX_TIME})
            script_args: Extra arguments to pass to the script

        Returns:
            A multi-line ground-truth summary of the submitted job (job_id, cluster,
            nodes, duration). Pass the job_id verbatim to get_hpc_job_status and
            other follow-up tools; report the rest as-is to the user without
            inventing default values.

        Available Jobs:

        {textwrap.indent(build_job_descriptions(), '        ').strip()}
    """).strip(),
)
async def submit_hpc_job(
    ctx: Context,
    job: str,
    cluster: Cluster | None = None,
    node_count: int | None = None,
    duration: str | None = None,
    script_args: str | None = None,
) -> str:
    if job not in AVAILABLE_JOBS:
        raise ValueError(f"{job} is not recognized, should be one of: {' '.join(AVAILABLE_JOBS)}")
    if node_count and (node_count > MAX_NODES or node_count <= 0):
        raise ValueError(f"node_count out of range (max {MAX_NODES})")
    duration_int = int(parse_time_limit(duration).total_seconds()) if duration else None
    if duration_int and (duration_int > MAX_TIME or duration_int < 1):
        raise ValueError(f"Time limit too large (max: {MAX_TIME})")

    cfg = get_user_config(ctx)
    cluster = _resolve_cluster(cluster, cfg)
    target = "odo" if cluster == "odo" else f"{settings.nersc_machine} (NERSC)"

    # TODO: Move confirm logic to the client side. MCP elicitation is not the right place for this,
    # but we're using it here for ease of migration.
    confirm_result = await ctx.elicit(
        message=f"Confirm running on {target}:\n" +
            get_tool_call_string('submit_hpc_job', job=job, cluster=cluster, node_count=node_count, duration=duration, script_args=script_args),
        response_type=Confirmation,
    )
    if confirm_result.action != "accept" or not confirm_result.data.confirm:
        raise Exception("Job submission cancelled by user")

    if cluster == "odo":
        job_id, eff_nodes, eff_duration = await _submit_odo_job(cfg, job, node_count, duration_int, script_args)
        _submitted_jobs[job_id] = SubmittedJob(cluster="odo")
    else:
        job_id, log_path, output_dir, eff_nodes, eff_duration = await _submit_perlmutter_job(
            cfg, job, node_count, duration_int, script_args,
        )
        _submitted_jobs[job_id] = SubmittedJob(
            cluster="perlmutter", log_path=log_path, output_dir=output_dir,
        )

    # Return a ground-truth summary so the LLM doesn't have to guess at submitted values.
    h, rem = divmod(eff_duration, 3600)
    m, s = divmod(rem, 60)
    return "\n".join([
        f"job_id: {job_id}",
        f"cluster: {cluster}",
        f"nodes: {eff_nodes}",
        f"duration: {h}:{m:02d}:{s:02d} ({eff_duration}s)",
    ])


async def _submit_odo_job(
    cfg: UserConfig, job: str, node_count: int | None, duration_int: int | None, script_args: str | None,
) -> tuple[str, int, int]:
    """ Returns (job_id, effective_node_count, effective_duration_seconds). """
    job_info = AVAILABLE_JOBS[job]
    odo_defaults = job_info.cluster_defaults.odo
    if odo_defaults is None:
        raise ValueError(f"Job '{job}' has no \"odo\" section in cluster_defaults.json")

    remote_hpc_jobs_dir = Path(require_remote_hpc_jobs_dir(cfg))
    remote_job_dir = remote_hpc_jobs_dir / settings.session_id / job
    s3m_client = get_s3m_client(s3m_token=require_s3m_token(cfg))
    check_result = await s3m_client.bash(f'[ -d {shlex.quote(str(remote_job_dir))} ] && echo true || echo false')
    if check_result.strip() != "true":
        await s3m_client.bash(f'mkdir -p -m 2775 {shlex.quote(str(remote_job_dir.parent))}')
        await s3m_client.upload(settings.local_hpc_jobs_dir / job, remote_job_dir)
        await s3m_client.bash(f'chmod -R g+rwX {shlex.quote(str(remote_job_dir))}')
        logging.info(f"Synced job to {remote_job_dir}")
        # TODO: should clean up the job script eventually, but don't want to do it on close as we may
        # want to leave jobs running between sessions.

    remote_job_script = remote_job_dir / "job.slurm"
    job_cmd = "\n".join([
        _render_hpc_setup_script(cfg),
        f"source {shlex.quote(str(remote_job_script))} {shlex.join(shlex.split(script_args or ''))}",
    ])

    resources_overrides = {
        "node_count": node_count,
    }
    spec = {
        "executable": "/bin/bash",
        "arguments": ["-l", "-c", job_cmd],
        "name": f"vista-{job}",
        "directory": str(remote_job_dir),
        "stdout_path": f"{remote_hpc_jobs_dir}/out/%j/log.out",
        "stderr_path": f"{remote_hpc_jobs_dir}/out/%j/log.out",
        "environment": {},
        "resources": {
            **odo_defaults.resources.model_dump(mode="json", exclude_none=True),
            **{k: v for k, v in resources_overrides.items() if v is not None},
        },
        "attributes": {
            "account": settings.hpc_account,
            "queue_name": "batch",
            "duration": odo_defaults.duration if duration_int is None else duration_int,
        },
    }
    response = await s3m_client.submit_job(spec)
    job_id = str(response.get("id") or "")
    if not job_id:
        raise ValueError(f"Failed to get job ID from S3M response: {response}")
    logging.info(f"Submitted job {job_id} via S3M to odo")
    eff_nodes = node_count or odo_defaults.resources.node_count or 1
    eff_duration = odo_defaults.duration if duration_int is None else duration_int
    return job_id, eff_nodes, eff_duration


async def _submit_perlmutter_job(
    cfg: UserConfig, job: str, node_count: int | None, duration_int: int | None, script_args: str | None,
) -> tuple[str, str, str, int, int]:
    """ Returns (job_id, rendered_log_path, rendered_output_dir, effective_node_count, effective_duration_seconds). """
    if not cfg.nersc_account:
        raise ToolError(
            "No NERSC account configured for this user. Set it in the Vista user "
            "settings page before submitting jobs to Perlmutter."
        )
    if not cfg.nersc_remote_dir:
        raise ToolError(
            "No NERSC remote dir configured for this user. Set it in the Vista user "
            "settings page before submitting jobs to Perlmutter."
        )

    job_info = AVAILABLE_JOBS[job]
    defaults = job_info.cluster_defaults.perlmutter
    if defaults is None:
        raise ValueError(f"Job '{job}' has no \"perlmutter\" section in cluster_defaults.json")

    local_job_dir = settings.local_hpc_jobs_dir / job
    job_script_path = local_job_dir / PERLMUTTER_JOB_SCRIPT
    if not job_script_path.exists():
        raise ValueError(
            f"Job '{job}' has no Perlmutter script at {job_script_path}. "
            f"Add a {PERLMUTTER_JOB_SCRIPT} alongside job.slurm to enable Perlmutter submission."
        )

    iri_client = await create_iri_client(iri_token=require_nersc_iri_token(cfg))
    base = cfg.nersc_remote_dir.rstrip('/')
    session_dir = f"{base}/{settings.session_id}"
    out_dir = f"{session_dir}/out"
    src_dir = f"{base}/{job}/src"
    await iri_client.mkdir(out_dir)
    await _sync_perlmutter_sources(iri_client, job, src_dir)

    job_script_text = job_script_path.read_text()
    setup_script_path = local_job_dir / PERLMUTTER_SETUP_SCRIPT
    pre_launch = (
        f"bash -lc {shlex.quote(setup_script_path.read_text())}"
        if setup_script_path.exists() else None
    )

    nodes = node_count or defaults.resources.node_count or 1
    workers_per_node = defaults.resources.processes_per_node or 1
    duration = duration_int or defaults.duration

    # Mirror Odo's hpc_setup_script_template UX: the user's job.perlmutter.slurm runs with
    # $VISTA_OUT set to a per-job-id output dir that's already mkdir'd.
    setup_snippet = textwrap.dedent(f"""
        export VISTA_OUT="{out_dir}/$SLURM_JOB_ID"
        mkdir -p "$VISTA_OUT"
    """).strip()
    job_cmd_args = shlex.join(shlex.split(script_args or ""))
    body_lines = [setup_snippet]
    if job_cmd_args:
        body_lines.append(f"set -- {job_cmd_args}")
    body_lines.append(job_script_text)
    job_cmd = "\n".join(body_lines) + "\n"

    # Convention-driven layout under VISTA_MCP_NERSC_REMOTE_DIR: each job gets a flat
    # <remote_dir>/<job>/{src,model} tree. The user's job.perlmutter.slurm reads
    # RUN_DIR_Perlmutter and FORGE_MODEL_Perlmutter from the job environment.
    # Advanced users can override either by setting iri.environment in cluster_defaults.json.
    iri_env = {
        "RUN_DIR_Perlmutter": src_dir,
        "FORGE_MODEL_Perlmutter": f"{base}/{job}/model",
    }
    iri_env.update(defaults.iri.environment)  # user-supplied JSON entries win

    # IRI image/module are surfaced as env vars so the user's job.perlmutter.slurm can
    # reference them in `srun shifter --image=$VISTA_PM_IMAGE` style invocations.
    if defaults.iri.image is not None:
        iri_env["VISTA_PM_IMAGE"] = defaults.iri.image
    if defaults.iri.module is not None:
        iri_env["VISTA_PM_MODULE"] = defaults.iri.module
    iri_env["SLURM_GPUS_PER_NODE"] = str(workers_per_node)

    stdout_template = f"{out_dir}/log-%j.out"
    stderr_template = f"{out_dir}/log-%j.err"

    spec = {
        "executable": "bash",
        "arguments": ["-l", "-c", job_cmd],
        "resources": {
            "node_count": nodes,
            "process_count": nodes * workers_per_node,
            "processes_per_node": workers_per_node,
            "cpu_cores_per_process": defaults.resources.cpu_cores_per_process,
            "exclusive_node_use": defaults.resources.exclusive_node_use,
        },
        "attributes": {
            "resource_id": iri_client.compute_resource_id,
            "queue_name": defaults.iri.queue_name,
            "account": cfg.nersc_account,
            "duration": duration,
            "custom_attributes": {"constraint": defaults.iri.constraint},
            **({"pre_launch": pre_launch} if pre_launch else {}),
            "directory": session_dir,
            "stdout_path": stdout_template,
            "stderr_path": stderr_template,
            "environment": iri_env,
        },
    }
    job_id = await iri_client.submit_job(spec, name=f"vista-{job}")
    logging.info(f"Submitted job {job_id} via IRI to {settings.nersc_machine}")
    return job_id, stdout_template.replace("%j", job_id), f"{out_dir}/{job_id}", nodes, duration


async def _sync_perlmutter_sources(iri_client, job: str, src_dir: str) -> None:
    """
    Upload `hpc_jobs/<job>/` (minus orchestration metadata) to `src_dir` via the IRI
    Filesystem API, mirroring Odo's SCP-on-first-submit pattern. Idempotent: if
    `src_dir` already has entries we skip the upload entirely.
    """
    try:
        ls_result = await iri_client.ls(src_dir)
        existing = _flatten_ls_paths(ls_result, root=src_dir)
        if existing:
            logging.debug(f"forge-tune src dir {src_dir} already populated ({len(existing)} entries); skipping upload")
            return
    except Exception as e:
        logging.debug(f"src dir {src_dir} not yet readable ({e}); creating and uploading")

    await iri_client.mkdir(src_dir)
    local_job_dir = settings.local_hpc_jobs_dir / job
    uploaded: list[str] = []
    for f in sorted(local_job_dir.iterdir()):
        if not f.is_file() or f.name.startswith(".") or f.name in PERLMUTTER_UPLOAD_SKIP:
            continue
        await iri_client.upload(f, f"{src_dir}/{f.name}")
        uploaded.append(f.name)
    logging.info(f"Uploaded {len(uploaded)} source file(s) to {src_dir}: {uploaded}")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
async def get_hpc_job_status(ctx: Context, job_id: str, cluster: Cluster | None = None) -> str:
    """
    Get the status and logs of a submitted HPC job.

    Args:
        job_id: The job id returned by submit_hpc_job
        cluster: Which cluster the job was submitted to. If omitted, looked up from the
            in-session cache; falls back to the only-configured cluster.
    """
    job_id = validate_job_id(job_id)
    cfg = get_user_config(ctx)
    cluster = _resolve_cluster(cluster, cfg, job_id)

    if cluster == "odo":
        return await _get_odo_job_status(cfg, job_id)
    return await _get_perlmutter_job_status(cfg, job_id)


async def _get_odo_job_status(cfg: UserConfig, job_id: str) -> str:
    s3m_client = get_s3m_client(s3m_token=require_s3m_token(cfg))
    job_data = await s3m_client.get_job_status(job_id)
    job_name = job_data.get("status", {}).get("meta_data", {}).get("s3m", {}).get("name", "")
    if not job_name.startswith("vista-"):
        raise ValueError(f"No vista job {job_id!r} found")

    metadata = {
        "JOB_ID": job_id,
        "CLUSTER": "odo",
        "STATE": job_data.get("status", {}).get("state", "UNKNOWN").upper(),
    }

    # Fetch logs and output file listing
    remote_hpc_jobs_dir = Path(require_remote_hpc_jobs_dir(cfg))
    output_dir = remote_hpc_jobs_dir / "out" / job_id
    log_path = output_dir / "log.out"
    logs = await s3m_client.bash(f"cat {shlex.quote(str(log_path))} 2>/dev/null || true")

    excludes = ['**/.venv*/*', '**/__pycache__/*']
    find_result = await s3m_client.bash(shlex.join([
        "find", str(output_dir),
        "-maxdepth", "3",
        "-type", "f",
        *itertools.chain(*[["-not", "-path", e] for e in excludes]),
    ]) + " 2>/dev/null")
    files = [
        str(Path(line.strip()).relative_to(output_dir))
        for line in find_result.strip().splitlines()
    ]
    files = files[:20]

    return "\n\n".join([
        "\n".join(f"{k}={v}" for k, v in metadata.items()),
        "--- LOGS ---",
        logs.strip() if logs.strip() else "(no logs yet)",
        "--- OUTPUT FILES ---",
        "\n".join(files) if files else "(no output files yet)",
    ])


async def _get_perlmutter_job_status(cfg: UserConfig, job_id: str) -> str:
    iri_client = await create_iri_client(iri_token=require_nersc_iri_token(cfg))
    status = await iri_client.get_job_status(job_id)
    state = status.get("state", "UNKNOWN").upper()

    metadata = {
        "JOB_ID": job_id,
        "CLUSTER": settings.nersc_machine,
        "STATE": state,
    }
    if status.get("exit_code") is not None:
        metadata["EXIT_CODE"] = status["exit_code"]
    if status.get("message"):
        metadata["MESSAGE"] = status["message"]

    submitted = _submitted_jobs.get(job_id)
    if submitted is None or submitted.log_path is None:
        return "\n\n".join([
            "\n".join(f"{k}={v}" for k, v in metadata.items()),
            "(no log path cached for this job; logs and outputs only available "
            "for jobs submitted in the current session)",
        ])

    try:
        logs = await iri_client.head(submitted.log_path, lines=200)
    except Exception as e:
        logs = f"(unable to fetch logs: {e})"

    files: list[str] = []
    if submitted.output_dir:
        try:
            ls_result = await iri_client.ls(submitted.output_dir, recursive=True)
            files = _flatten_ls_paths(ls_result, root=submitted.output_dir)[:20]
        except Exception as e:
            logging.info(f"output dir not readable yet ({submitted.output_dir}): {e}")

    return "\n\n".join([
        "\n".join(f"{k}={v}" for k, v in metadata.items()),
        "--- LOGS ---",
        logs.strip() if logs.strip() else "(no logs yet)",
        "--- OUTPUT FILES ---",
        "\n".join(files) if files else "(no output files yet)",
    ])


def _flatten_ls_paths(ls_result: dict, *, root: str) -> list[str]:
    """
    Walk an amscrot ls() result and return file paths relative to *root*.

    The IRI ls response shape isn't strictly typed; this is forgiving — it accepts
    either a list of entries or a dict with a "files"/"entries"/"results" key.
    """
    entries: list[dict] = []
    if isinstance(ls_result, list):
        entries = ls_result
    elif isinstance(ls_result, dict):
        for key in ("files", "entries", "results", "items"):
            v = ls_result.get(key)
            if isinstance(v, list):
                entries = v
                break
    out: list[str] = []
    root_norm = root.rstrip("/")
    for e in entries:
        if not isinstance(e, dict):
            continue
        path = e.get("path") or e.get("name") or ""
        if not path:
            continue
        if path.startswith(root_norm + "/"):
            out.append(path[len(root_norm) + 1:])
        else:
            out.append(path)
    return out


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
async def get_hpc_job_outputs(
    ctx: Context, job_id: str, files: list[str], cluster: Cluster | None = None,
) -> str:
    """
    Download output files from an HPC job.

    Args:
        job_id: The job id returned by submit_hpc_job
        files: List of file paths to download. Relative to the jobs output directory (as shown by get_hpc_job_status).
        cluster: Which cluster the job was submitted to. If omitted, looked up from the
            in-session cache.

    Returns:
        The downloaded file paths.
    """
    job_id = validate_job_id(job_id)
    cfg = get_user_config(ctx)
    cluster = _resolve_cluster(cluster, cfg, job_id)

    if cluster == "odo":
        return await _get_odo_job_outputs(cfg, job_id, files)
    return await _get_perlmutter_job_outputs(cfg, job_id, files)


async def _get_odo_job_outputs(cfg: UserConfig, job_id: str, files: list[str]) -> str:
    remote_hpc_jobs_dir = Path(require_remote_hpc_jobs_dir(cfg))
    remote_out_dir = remote_hpc_jobs_dir / "out" / job_id
    local_out_dir = settings.output_dir / job_id
    sandbox_out_dir = Path("/mnt/data/output") / job_id

    s3m_client = get_s3m_client(s3m_token=require_s3m_token(cfg))
    downloaded = []
    for file in files:
        remote_path = Path(os.path.normpath(remote_out_dir / file))
        if not remote_path.is_relative_to(remote_out_dir):
            raise ValueError(f'Invalid path "{file}", must be under job output dir')
        local_path = local_out_dir / remote_path.relative_to(remote_out_dir)
        sandbox_path = sandbox_out_dir / remote_path.relative_to(remote_out_dir)

        local_path.parent.mkdir(parents=True, exist_ok=True)
        await s3m_client.download(remote_path, local_path)
        downloaded.append(str(sandbox_path))

    return "Downloaded files:\n" + "\n".join(downloaded)


async def _get_perlmutter_job_outputs(cfg: UserConfig, job_id: str, files: list[str]) -> str:
    # IRI filesystem download is currently text-only; binary checkpoints are not supported here.
    submitted = _submitted_jobs.get(job_id)
    if submitted is None or submitted.output_dir is None:
        raise ValueError(
            f"No output directory cached for job {job_id!r}. Output retrieval is only "
            f"available for Perlmutter jobs submitted in the current session."
        )

    iri_client = await create_iri_client(iri_token=require_nersc_iri_token(cfg))
    local_out_dir = settings.output_dir / job_id
    sandbox_out_dir = Path("/mnt/data/output") / job_id

    downloaded = []
    for file in files:
        if ".." in Path(file).parts or Path(file).is_absolute():
            raise ValueError(f'Invalid path "{file}"')
        remote_path = f"{submitted.output_dir.rstrip('/')}/{file}"
        local_path = local_out_dir / file
        sandbox_path = sandbox_out_dir / file
        local_path.parent.mkdir(parents=True, exist_ok=True)
        await iri_client.download(remote_path, local_path)
        downloaded.append(str(sandbox_path))

    return "Downloaded files:\n" + "\n".join(downloaded)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
async def list_hpc_jobs(ctx: Context, cluster: Cluster | None = None) -> str:
    """
    List recently submitted HPC jobs and their states.

    Args:
        cluster: Which cluster to list jobs for. If omitted, lists from the only-configured cluster.
    """
    cfg = get_user_config(ctx)
    cluster = _resolve_cluster(cluster, cfg)
    if cluster == "odo":
        return await _list_odo_jobs(cfg)
    return _list_perlmutter_jobs()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True))
async def cancel_hpc_job(ctx: Context, job_id: str, cluster: Cluster | None = None) -> str:
    """
    Cancel a queued or running HPC job.

    Args:
        job_id: The job id returned by submit_hpc_job.
        cluster: Which cluster the job was submitted to. If omitted, looked up from
            the in-session cache.

    Returns:
        Confirmation of the cancellation request.
    """
    job_id = validate_job_id(job_id)
    cfg = get_user_config(ctx)
    cluster = _resolve_cluster(cluster, cfg, job_id)
    if cluster == "odo":
        # S3M doesn't expose cancel directly; scancel over the existing SSH session works.
        s3m_client = get_s3m_client(s3m_token=require_s3m_token(cfg))
        await s3m_client.bash(f"scancel {shlex.quote(job_id)}")
    else:
        iri_client = await create_iri_client(iri_token=require_nersc_iri_token(cfg))
        await iri_client.cancel_job(job_id)
    logging.info(f"Cancelled job {job_id} on {cluster}")
    return f"Cancellation requested for job {job_id} on {cluster}."


async def _list_odo_jobs(cfg: UserConfig) -> str:
    # /api/v1/compute/status/{resource_id} should work but has some odd behavior around "historical" currently
    # I think it only looks up very recent jobs. We may need to rethink how handle the job list
    s3m_client = get_s3m_client(s3m_token=require_s3m_token(cfg))
    td = timedelta(hours=1)
    sacct_out = await s3m_client.bash("TZ=UTC " + shlex.join([
        "sacct", "--json", "--allocations",
        "--starttime", (datetime.now(timezone.utc) - td).strftime("%Y-%m-%dT%H:%M:%S"),
        "--user", f"{settings.hpc_account}_auser",
    ]))
    sacct_jobs = json.loads(sacct_out)["jobs"]

    lines = []
    for job in sacct_jobs:
        if not job["name"].startswith("vista-"):
            continue
        job_id = str(job["job_id"])
        state = job["state"]["current"][0]
        lines.append(f"{job_id} {state}")

    return "\n".join(lines) if lines else "No jobs found."


def _list_perlmutter_jobs() -> str:
    # IRI doesn't expose user-job listing. Fall back to the in-process cache of jobs
    # submitted in this session.
    ids = [jid for jid, c in _submitted_jobs.items() if c == "perlmutter"]
    if not ids:
        return "No Perlmutter jobs submitted in this session."
    return "\n".join(ids)
