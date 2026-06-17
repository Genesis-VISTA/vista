"""
MCP for remote HPC job submission. Dispatches between:
- Odo (OLCF, open enclave) via the AmSC IRI API (`lib/iri.py`) + Globus file ops (`lib/globus.py`)
- Frontier (OLCF, moderate enclave) via the AmSC IRI API + Globus file ops
- Perlmutter (NERSC) via the NERSC IRI API and amscrot SDK (`lib/iri.py`)
"""
from __future__ import annotations
import json, logging, os, shlex, textwrap, time, dataclasses
from pathlib import Path
from typing import Literal

from fastmcp import FastMCP, Context
from fastmcp.exceptions import ToolError
from pydantic import BaseModel
from mcp.types import ToolAnnotations

from .config import settings
from .lib.iri import (
    IriClient, IriDefaults, create_iri_client, create_odo_iri_client, create_olcf_iri_client,
)
from .lib.globus import GlobusClient, create_globus_client
from .lib.olcf_token import require_s3m_project
from .lib.user_config import UserConfig, get_vista_meta
from .lib.misc import parse_time_limit, validate_job_id


Cluster = Literal["odo", "perlmutter", "frontier"]
""" Supported HPC clusters. """

PERLMUTTER_JOB_SCRIPT = "job.perlmutter.slurm"
PERLMUTTER_SETUP_SCRIPT = "setup_perlmutter.sh"
""" Optional pre_launch setup script in each job dir; inlined into JobSpec.attributes.pre_launch. """

FRONTIER_JOB_SCRIPT = "job.frontier.slurm"
FRONTIER_SETUP_SCRIPT = "setup_frontier.sh"

ODO_JOB_SCRIPT = "job.odo.slurm"
ODO_SETUP_SCRIPT = "setup_odo.sh"

# Orchestration metadata files Vista uses to drive submission — never uploaded to remote
# RUN_DIR (each cluster-dispatcher inlines them differently into the JobSpec).
_HPC_JOB_METADATA_FILES = {
    "README.md",
    "cluster_defaults.json",
    "s3m_defaults.json",      # legacy from before the rename
    "job.slurm",              # legacy Odo script name from before the per-cluster suffix
    ODO_JOB_SCRIPT,
    ODO_SETUP_SCRIPT,
    PERLMUTTER_JOB_SCRIPT,
    PERLMUTTER_SETUP_SCRIPT,
    FRONTIER_JOB_SCRIPT,
    FRONTIER_SETUP_SCRIPT,
}


class ClusterDefaults(BaseModel):
    """
    Per-job defaults loaded from `<job>/cluster_defaults.json`. A job opts in to a cluster
    by including the corresponding section.
    """
    odo: IriDefaults | None = None
    perlmutter: IriDefaults | None = None
    frontier: IriDefaults | None = None


@dataclasses.dataclass
class JobInfo:
    name: str
    description: str
    cluster_defaults: ClusterDefaults


_CLUSTER_JOB_SCRIPTS = (ODO_JOB_SCRIPT, PERLMUTTER_JOB_SCRIPT, FRONTIER_JOB_SCRIPT)


def get_available_jobs() -> dict[str, JobInfo]:
    jobs = {}
    for file in settings.local_hpc_jobs_dir.iterdir():
        if file.is_dir():
            if not any((file / s).exists() for s in _CLUSTER_JOB_SCRIPTS):
                raise ValueError(
                    f"Job {file} has no job script; add at least one of: "
                    f"{', '.join(_CLUSTER_JOB_SCRIPTS)}"
                )
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

_LOG_CACHE_TTL_S = 30
"""
How long `_get_olcf_job_status` will serve a previously-fetched log file
from the local filesystem before re-fetching via Globus. Trades log freshness
for fast back-to-back status calls within a single chat turn. Set to 0 to
disable caching (always re-fetch).
"""

_PRE_RUN_STATES = {"NEW", "QUEUED", "PENDING", "HELD"}
"""
IRI/PSI-J job states in which the job hasn't touched a compute node yet, so no
log file or output dir can exist — status queries skip the Globus round trips
entirely while the job is in one of these.
"""


mcp = FastMCP("Submit Job")


@dataclasses.dataclass
class SubmittedJob:
    cluster: Cluster
    # Rendered absolute paths after %j substitution (all clusters).
    log_path: str | None = None
    output_dir: str | None = None


# Durable registry of job_id -> SubmittedJob. Used by get_hpc_job_status /
# get_hpc_job_outputs / list_hpc_jobs to dispatch when the caller doesn't pass an
# explicit `cluster` arg, and to resolve per-job log/output paths.
#
# Persisted to disk (see _persist_submitted_jobs) and reloaded on startup so a job
# submitted before an MCP-server restart stays pollable: the rendered log/output
# paths can't be recomputed after the fact (they depend on the submitting session's
# session_id), so we remember them.
_submitted_jobs: dict[str, SubmittedJob] = {}


def _registry_path() -> Path:
    """ On-disk location of the persisted job registry. """
    return settings.data_dir / "hpc_job_registry.json"


def _serialize_jobs(jobs: dict[str, SubmittedJob]) -> dict[str, dict]:
    return {
        jid: {"cluster": s.cluster, "log_path": s.log_path, "output_dir": s.output_dir}
        for jid, s in jobs.items()
    }


def _deserialize_jobs(data: dict) -> dict[str, SubmittedJob]:
    jobs: dict[str, SubmittedJob] = {}
    for jid, rec in data.items():
        if not isinstance(rec, dict) or "cluster" not in rec:
            continue  # skip malformed entries rather than failing the whole load
        jobs[jid] = SubmittedJob(
            cluster=rec["cluster"],
            log_path=rec.get("log_path"),
            output_dir=rec.get("output_dir"),
        )
    return jobs


def _load_submitted_jobs(path: Path | None = None) -> dict[str, SubmittedJob]:
    """ Read the persisted registry. Missing or corrupt file -> empty dict (best-effort). """
    path = path or _registry_path()
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logging.warning(f"Ignoring unreadable HPC job registry at {path}: {e}")
        return {}
    if not isinstance(data, dict):
        return {}
    return _deserialize_jobs(data)


def _persist_submitted_jobs(path: Path | None = None) -> None:
    """ Atomically write the current registry. Best-effort: never raises into a tool call. """
    path = path or _registry_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w") as f:
            json.dump(_serialize_jobs(_submitted_jobs), f)
        os.replace(tmp, path)
    except OSError as e:
        logging.warning(f"Could not persist HPC job registry to {path}: {e}")


def _record_submitted_job(job_id: str, submitted: SubmittedJob) -> None:
    """ Add a job to the in-memory registry and persist the registry to disk. """
    _submitted_jobs[job_id] = submitted
    _persist_submitted_jobs()


# Rehydrate from disk at import so a restarted server remembers prior submissions.
_submitted_jobs.update(_load_submitted_jobs())


def _default_cluster(cfg: UserConfig) -> Cluster:
    """
    Return the only configured cluster. Raises if zero or multiple are configured.

    Each S3M token is per-cluster (group-scoped to one OLCF project), so the
    presence of a token directly selects the cluster:

    - `odo_s3m_token` enables Odo.
    - `frontier_s3m_token` enables Frontier.
    - `nersc_iri_token` enables Perlmutter.
    """
    configured: list[Cluster] = []
    if cfg.odo_s3m_token:
        configured.append("odo")
    if cfg.frontier_s3m_token:
        configured.append("frontier")
    if cfg.nersc_iri_token:
        configured.append("perlmutter")

    if len(configured) == 1:
        return configured[0]
    if len(configured) > 1:
        choices = ", ".join(f'"{c}"' for c in configured)
        raise ToolError(
            f"Multiple HPC clusters configured ({', '.join(configured)}); please pass cluster={choices}"
        )
    raise ToolError(
        "No HPC cluster configured for this user. Add an S3M token (Odo / Frontier) "
        "or a NERSC IRI token (Perlmutter) in the user settings page."
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


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True),
    description=textwrap.dedent(f"""
        Submit a job to the HPC system.

        Args:
            job: The name of the job to run (available jobs: {' '.join(AVAILABLE_JOBS.keys())})
            cluster: Which cluster to submit to ("odo", "frontier", or "perlmutter"). If only
                one cluster is configured, this can be omitted. Odo and Frontier use OLCF's
                IRI service (compute) plus Globus (files), each with its own per-enclave S3M
                token; Perlmutter uses NERSC IRI.
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

    cfg = get_vista_meta(ctx).user
    cluster = _resolve_cluster(cluster, cfg)
    target = {
        "odo": "odo",
        "frontier": "frontier",
        "perlmutter": f"{settings.nersc_machine} (NERSC)",
    }[cluster]

    if cluster == "odo":
        job_id, log_path, output_dir, eff_nodes, eff_duration = await _submit_odo_job(
            cfg, job, node_count, duration_int, script_args,
        )
    elif cluster == "perlmutter":
        job_id, log_path, output_dir, eff_nodes, eff_duration = await _submit_perlmutter_job(
            cfg, job, node_count, duration_int, script_args,
        )
    else:  # "frontier"
        job_id, log_path, output_dir, eff_nodes, eff_duration = await _submit_frontier_job(
            cfg, job, node_count, duration_int, script_args,
        )

    # Record + persist so status/outputs survive an MCP-server restart (see _persist_submitted_jobs).
    _record_submitted_job(
        job_id, SubmittedJob(cluster=cluster, log_path=log_path, output_dir=output_dir)
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
) -> tuple[str, str, str, int, int]:
    """
    Odo dispatch (OLCF open enclave): IRI compute + Globus file ops, the same
    architecture as Frontier (`_submit_frontier_job`). Differences:

    - open-enclave IRI endpoint (`settings.odo_iri_url`) with a pinned compute
      resource id (`settings.odo_compute_resource_id`)
    - the Slurm account is the global `settings.odo_account` (one shared OLCF
      project for all Vista users); the user's S3M token must belong to it
    - the remote base is the preset `settings.odo_remote_dir`
    - the setup snippet `cd`s into RUN_DIR_Odo so job.odo.slurm scripts that
      reference sources relative to the working dir keep working
    - Globus NEVER creates the output dir (see below); an admin pre-creates
      `<odo_remote_dir>/out` once with `mkdir -p -m 2775`

    Permissions model: Globus mkdir/transfer runs as the user's mapped account
    with the DTN's umask, so Globus-created dirs are NOT group-writable, and
    unlike Frontier, Odo has no setfacl to grant the IRI automation user
    (<project>_auser) write access after the fact. Globus is therefore only
    allowed to create/write things the auser merely READS (the `<job>/src`
    tree). Everything the auser WRITES lives under the pre-created,
    group-writable `<base>/out`: Slurm logs land directly in it, and the
    per-job `$VISTA_OUT` subdir is mkdir'd at runtime by the auser itself.

    Returns (job_id, rendered_log_path, rendered_output_dir, effective_node_count, effective_duration_seconds).
    """
    job_info = AVAILABLE_JOBS[job]
    defaults = job_info.cluster_defaults.odo
    if defaults is None:
        raise ValueError(f"Job '{job}' has no \"odo\" section in cluster_defaults.json")

    if not settings.vista_globus_collection_id:
        raise ToolError(
            "Vista's Globus collection is not set up on this deployment. "
            "Odo file ops go through Globus; run ./scripts/launch_globus.py to expose a "
            "Globus collection covering both local_hpc_jobs_dir and output_dir."
        )

    local_job_dir = settings.local_hpc_jobs_dir / job
    job_script_path = local_job_dir / ODO_JOB_SCRIPT
    if not job_script_path.exists():
        raise ValueError(
            f"Job '{job}' has no Odo script at {job_script_path}. "
            f"Add a {ODO_JOB_SCRIPT} to enable Odo submission."
        )

    await _require_olcf_access(cfg, "odo")
    iri_client = await create_odo_iri_client(iri_token=cfg.require_s3m_token("odo"))
    globus = create_globus_client(refresh_token=settings.require_globus_token("odo"))
    base = settings.odo_remote_dir.rstrip('/')
    # No session prefix: job ids are unique, and the out dir must be the
    # pre-created group-writable one — a fresh per-session dir would have to be
    # created by Globus, which is exactly what breaks auser write access.
    out_dir = f"{base}/out"
    src_dir = f"{base}/{job}/src"

    await _require_odo_out_dir(globus, base=base, out_dir=out_dir)
    await _sync_job_sources(
        globus, job, src_dir, base=base,
        remote_endpoint=settings.odo_globus_collection_id,
    )

    job_script_text = job_script_path.read_text()
    setup_script_path = local_job_dir / ODO_SETUP_SCRIPT
    pre_launch = (
        f"bash -lc {shlex.quote(setup_script_path.read_text())}"
        if setup_script_path.exists() else None
    )

    nodes = node_count or defaults.resources.node_count or 1
    workers_per_node = defaults.resources.processes_per_node or 1
    duration = duration_int or defaults.duration

    # Mirrors Frontier's setup snippet (proxy because Odo compute nodes have no
    # direct outbound network, HOME fallback for amscrot's bare IRI env, module
    # purge against host-env Lmod contamination via --export=ALL) plus a `cd`
    # into the synced source dir to preserve the job.odo.slurm contract of
    # running from the job directory.
    setup_snippet = textwrap.dedent(f"""
        export VISTA_OUT="{out_dir}/$SLURM_JOB_ID"
        mkdir -p -m 2775 "$VISTA_OUT"

        export HOME="${{HOME:-$VISTA_OUT}}"

        export https_proxy="http://proxy.ccs.ornl.gov:3128"
        export http_proxy="http://proxy.ccs.ornl.gov:3128"
        export no_proxy="localhost,127.0.0.1,0.0.0.0"

        module purge 2>/dev/null || true

        cd "{src_dir}"
    """).strip()
    job_cmd_args = shlex.join(shlex.split(script_args or ""))
    body_lines = [setup_snippet]
    if job_cmd_args:
        body_lines.append(f"set -- {job_cmd_args}")
    body_lines.append(job_script_text)
    job_cmd = "\n".join(body_lines) + "\n"

    # Odo-suffixed env vars, mirroring RUN_DIR_Frontier / RUN_DIR_Perlmutter.
    iri_env = {
        "RUN_DIR_Odo": src_dir,
        "FORGE_MODEL_Odo": f"{base}/{job}/model",
    }
    iri_env.update(defaults.iri.environment)  # user-supplied JSON entries win

    if defaults.iri.image is not None:
        iri_env["VISTA_ODO_IMAGE"] = defaults.iri.image
    if defaults.iri.module is not None:
        iri_env["VISTA_ODO_MODULE"] = defaults.iri.module

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
            "account": settings.odo_account,
            "duration": duration,
            **({"custom_attributes": {"constraint": defaults.iri.constraint}} if defaults.iri.constraint else {}),
            **({"pre_launch": pre_launch} if pre_launch else {}),
            "directory": base,
            "stdout_path": stdout_template,
            "stderr_path": stderr_template,
            "environment": iri_env,
        },
    }
    job_id = await iri_client.submit_job(spec, name=f"vista-{job}")
    logging.info(f"Submitted job {job_id} via IRI to odo")
    return job_id, stdout_template.replace("%j", job_id), f"{out_dir}/{job_id}", nodes, duration


async def _require_odo_out_dir(globus: GlobusClient, *, base: str, out_dir: str) -> None:
    """
    Verify the pre-created, group-writable `<base>/out` exists before submitting.

    We deliberately do NOT create it via Globus — the DTN applies the user's
    umask, so a Globus-made dir would deny the IRI automation user write access
    and the job would die at Slurm-log creation with no useful error. Checking
    up front turns that into an actionable message. Globus `ls` reports a
    `permissions` octal string per entry; group-write is the bit we need.
    """
    try:
        entries = await globus.operation_ls(
            endpoint=settings.odo_globus_collection_id, path=base,
        )
    except Exception as e:
        raise ToolError(
            f"Cannot list {base} on the Odo Globus collection ({e}). Check that "
            "VISTA_MCP_ODO_REMOTE_DIR exists on Odo and that the deployment's "
            "Globus identity has access to it."
        )
    out_entry = next(
        (e for e in entries if e.get("name") == "out" and e.get("type") == "dir"),
        None,
    )
    if out_entry is None:
        raise ToolError(
            f"Missing output directory {out_dir} on Odo. One-time setup — log in "
            f"to Odo and run:\n  mkdir -p -m 2775 {out_dir}\nThis lets the IRI "
            "automation user write job logs and outputs (Globus cannot create "
            "group-writable directories, and Odo has no setfacl)."
        )
    perms = out_entry.get("permissions")
    if perms and not (int(perms, 8) & 0o020):
        raise ToolError(
            f"{out_dir} exists but is not group-writable (permissions {perms}), so "
            f"the IRI automation user cannot write job logs there. Run on Odo:\n"
            f"  chmod 2775 {out_dir}"
        )


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
            f"Add a {PERLMUTTER_JOB_SCRIPT} to enable Perlmutter submission."
        )

    iri_client = await create_iri_client(iri_token=cfg.require_nersc_iri_token())
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

    # Mirror the Odo/Frontier setup-snippet UX: the user's job.perlmutter.slurm runs
    # with $VISTA_OUT set to a per-job-id output dir that's already mkdir'd.
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
        if not f.is_file() or f.name.startswith(".") or f.name in _HPC_JOB_METADATA_FILES:
            continue
        await iri_client.upload(f, f"{src_dir}/{f.name}")
        uploaded.append(f.name)
    logging.info(f"Uploaded {len(uploaded)} source file(s) to {src_dir}: {uploaded}")


async def _submit_frontier_job(
    cfg: UserConfig, job: str, node_count: int | None, duration_int: int | None, script_args: str | None,
) -> tuple[str, str, str, int, int]:
    """
    Frontier dispatch (OLCF moderate enclave).

    Compute lives on the AmSC IRI service at `settings.frontier_iri_url`. File
    ops (mkdir / source upload / log fetch / output download) go through Globus
    via `lib/globus.py` — the OLCF moderate-enclave token's
    `iri-frontend-moderate` scope doesn't authorize IRI storage discovery, so
    the deployment-wide Globus refresh token grants access to the OLCF DTN.

    The Slurm account is the global `settings.frontier_account` (one shared
    OLCF project for all Vista users); the user's S3M token must belong to it.

    Returns (job_id, rendered_log_path, rendered_output_dir, effective_node_count, effective_duration_seconds).
    """
    job_info = AVAILABLE_JOBS[job]
    defaults = job_info.cluster_defaults.frontier
    if defaults is None:
        raise ValueError(f"Job '{job}' has no \"frontier\" section in cluster_defaults.json")

    local_job_dir = settings.local_hpc_jobs_dir / job
    job_script_path = local_job_dir / FRONTIER_JOB_SCRIPT
    if not job_script_path.exists():
        raise ValueError(
            f"Job '{job}' has no Frontier script at {job_script_path}. "
            f"Add a {FRONTIER_JOB_SCRIPT} to enable Frontier submission."
        )

    if not settings.vista_globus_collection_id:
        raise ToolError(
            "Vista's Globus collection is not set up on this deployment. "
            "Frontier file ops go through Globus; run ./scripts/launch_globus.py to expose a "
            "Globus collection covering both local_hpc_jobs_dir and output_dir."
        )

    await _require_olcf_access(cfg, "frontier")
    iri_client = await create_olcf_iri_client(iri_token=cfg.require_s3m_token("frontier"))
    globus = create_globus_client(refresh_token=settings.require_globus_token("frontier"))
    base = settings.frontier_remote_dir.rstrip('/')
    session_dir = f"{base}/{settings.session_id}"
    out_dir = f"{session_dir}/out"
    src_dir = f"{base}/{job}/src"

    # File ops via Globus. The OLCF DTN's mkdir doesn't take a mode, so the
    # parent `frontier_remote_dir` must have been one-time chmod'd to 2775
    # (setgid + g+rwx) so created subdirs inherit group + setgid. Without
    # that, the IRI service's automation user (e.g. chm243_auser) can't
    # traverse Vista-created dirs and Slurm's prolog kills the job at
    # startup. See README → Frontier setup.
    #
    # Globus's MKD is one-level-only; we walk under `base` to create
    # <session>/out and <job>/src (two levels each).
    await globus.operation_mkdir_p(
        endpoint=settings.frontier_globus_collection_id,
        path=out_dir,
        parents_below=base,
    )
    await _sync_job_sources(
        globus, job, src_dir, base=base,
        remote_endpoint=settings.frontier_globus_collection_id,
    )

    job_script_text = job_script_path.read_text()
    setup_script_path = local_job_dir / FRONTIER_SETUP_SCRIPT
    pre_launch = (
        f"bash -lc {shlex.quote(setup_script_path.read_text())}"
        if setup_script_path.exists() else None
    )

    nodes = node_count or defaults.resources.node_count or 1
    workers_per_node = defaults.resources.processes_per_node or 1
    duration = duration_int or defaults.duration

    # Frontier compute nodes have no direct outbound network — pip / curl / git
    # against the public internet need the OLCF HTTP proxy. Matches the Odo setup
    # snippet (_submit_odo_job) so user scripts don't have to know.
    #
    # HOME fallback: amscrot's IRI service env doesn't carry HOME, and a missing
    # HOME makes conda activation hooks (run by `module load xforge`) silently
    # break — Python then segfaults at import. Default to VISTA_OUT (guaranteed
    # writable, per-job-id) so conda/pip caches go somewhere sane.
    #
    # `module purge` clears any modules already loaded in amscrot's IRI service
    # host env (Lmod state propagates via --export=ALL, which leaves Cray PE
    # modules stacked with refcount > 1 when the job script then does
    # `module load xforge` — stacked libsci/PE in LD_LIBRARY_PATH then conflicts
    # with the xforge-provided versions and PyTorch segfaults at import).
    setup_snippet = textwrap.dedent(f"""
        export VISTA_OUT="{out_dir}/$SLURM_JOB_ID"
        mkdir -p "$VISTA_OUT"

        export HOME="${{HOME:-$VISTA_OUT}}"

        export https_proxy="http://proxy.ccs.ornl.gov:3128"
        export http_proxy="http://proxy.ccs.ornl.gov:3128"
        export no_proxy="localhost,127.0.0.1,0.0.0.0"

        module purge 2>/dev/null || true
    """).strip()
    job_cmd_args = shlex.join(shlex.split(script_args or ""))
    body_lines = [setup_snippet]
    if job_cmd_args:
        body_lines.append(f"set -- {job_cmd_args}")
    body_lines.append(job_script_text)
    job_cmd = "\n".join(body_lines) + "\n"

    # Frontier-suffixed env vars so the user's job.frontier.slurm can pull them in
    # without colliding with Perlmutter's _Perlmutter-suffixed names.
    iri_env = {
        "RUN_DIR_Frontier": src_dir,
        "FORGE_MODEL_Frontier": f"{base}/{job}/model",
    }
    iri_env.update(defaults.iri.environment)  # user-supplied JSON entries win

    if defaults.iri.image is not None:
        iri_env["VISTA_FR_IMAGE"] = defaults.iri.image
    if defaults.iri.module is not None:
        iri_env["VISTA_FR_MODULE"] = defaults.iri.module
    # NOTE: we do NOT set SLURM_GPUS_PER_NODE here. On Frontier with `--exclusive`
    # the prolog already binds all 8 GCDs and exports SLURM_GPUS_ON_NODE=8 plus
    # SLURM_JOB_GPUS=0..7 automatically — setting SLURM_GPUS_PER_NODE is redundant.
    # (Perlmutter's dispatch sets it because shifter reads it for per-task GCD
    # binding inside the container; Frontier doesn't use shifter here.)

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
            "account": settings.frontier_account,
            "duration": duration,
            **({"custom_attributes": {"constraint": defaults.iri.constraint}} if defaults.iri.constraint else {}),
            **({"pre_launch": pre_launch} if pre_launch else {}),
            "directory": session_dir,
            "stdout_path": stdout_template,
            "stderr_path": stderr_template,
            "environment": iri_env,
            # Keep `inherit_environment` at its default (True). Setting False strips
            # the IRI service host's LD_LIBRARY_PATH / PYTHONPATH but ALSO blocks
            # Slurm's SLURM_* vars from reaching the batch step on Frontier (set -u
            # then trips on SLURM_NNODES, etc.). Instead, the job.frontier.slurm
            # script `unset`s the contaminated library/path vars BEFORE module load
            # — that keeps SLURM_* intact while giving `module load xforge` a clean
            # slate.
        },
    }
    job_id = await iri_client.submit_job(spec, name=f"vista-{job}")
    logging.info(f"Submitted job {job_id} via IRI to {settings.frontier_machine}")
    return job_id, stdout_template.replace("%j", job_id), f"{out_dir}/{job_id}", nodes, duration


async def _sync_job_sources(
    globus: GlobusClient, job: str, src_dir: str, *, base: str, remote_endpoint: str,
) -> None:
    """
    Upload `hpc_jobs/<job>/` (minus orchestration metadata) to `src_dir` via a
    single Globus transfer task (Vista's GCS → `remote_endpoint`). Shared by the
    Odo and Frontier dispatchers — they differ only in which OLCF collection
    `remote_endpoint` points at. Idempotent: if `src_dir` already has entries,
    the upload is skipped.

    `base` is the cluster's remote base dir (assumed pre-existing); we use it
    as the parents_below floor for the recursive mkdir.

    Permissions: the IRI auto-user (e.g. gen150_auser / chm243_auser) only
    needs to READ the src tree, which the DTN's default umask grants
    (755 dirs / 644 files); correct group ownership is inherited from `base`,
    which the user one-time `chmod 2775`'d. Globus-created dirs are NOT
    group-writable — anything the auto-user must WRITE has to live elsewhere
    (see `_require_odo_out_dir` for Odo; Frontier relies on setfacl).
    Pre-creating `<base>/<job>/src` manually also works — the mkdir here is
    idempotent and the transfer just adds files.
    """
    # Probe the remote collection: if src_dir exists and contains entries, skip upload.
    try:
        existing = await globus.operation_ls(endpoint=remote_endpoint, path=src_dir)
        if existing:
            logging.debug(f"src dir {src_dir} already populated; skipping upload")
            return
    except Exception as e:
        # src_dir doesn't exist yet (or is unreachable for some other reason);
        # fall through to mkdir + transfer.
        logging.debug(f"src dir {src_dir} not yet readable ({e}); creating + uploading")

    # mkdir -p `<base>/<job>/src` — Globus needs both levels created explicitly.
    await globus.operation_mkdir_p(endpoint=remote_endpoint, path=src_dir, parents_below=base)

    # Stage the upload set: scan local_hpc_jobs_dir/<job> for files to push.
    # Vista's GCS sees the same paths Python sees (the deployment is expected to
    # expose at least the entire `local_hpc_jobs_dir` tree via the collection).
    local_job_dir = settings.local_hpc_jobs_dir / job
    items: list[tuple[str, str, bool]] = []
    for f in sorted(local_job_dir.iterdir()):
        if not f.is_file() or f.name.startswith(".") or f.name in _HPC_JOB_METADATA_FILES:
            continue
        items.append((str(f), f"{src_dir}/{f.name}", False))

    if not items:
        logging.warning(f"no source files to upload from {local_job_dir} (only metadata?)")
        return

    await globus.transfer_and_wait(
        src_endpoint=settings.vista_globus_collection_id,
        dst_endpoint=remote_endpoint,
        items=items,
        label=f"vista source upload: {job}",
    )
    logging.info(f"Uploaded {len(items)} source file(s) to {src_dir}: {[Path(s).name for s, _, _ in items]}")


async def _create_olcf_iri_for(cluster: Cluster, cfg: UserConfig) -> IriClient:
    """ IRI client for an OLCF cluster: "odo" (open enclave) or "frontier" (moderate). """
    if cluster == "odo":
        return await create_odo_iri_client(iri_token=cfg.require_s3m_token("odo"))
    return await create_olcf_iri_client(iri_token=cfg.require_s3m_token("frontier"))


def _olcf_collection_id(cluster: Cluster) -> str:
    """ Globus collection exposing the cluster's filesystem. """
    if cluster == "odo":
        return settings.odo_globus_collection_id
    return settings.frontier_globus_collection_id


async def _require_olcf_access(cfg: UserConfig, cluster: Cluster) -> None:
    """
    Verify the user's S3M token belongs to the cluster's OLCF project before
    any file op. Globus transfers run under Vista's own identity against
    project-shared directories, so this introspection is what authorizes the
    user — it must guard every path that touches Globus, including
    `_get_olcf_job_outputs`, which never calls IRI.
    """
    if cluster == "odo":
        account, url = settings.odo_account, settings.odo_introspect_url
    else:
        account, url = settings.frontier_account, settings.frontier_introspect_url
    await require_s3m_project(
        cfg.require_s3m_token(cluster), account, cluster=cluster, introspect_url=url,
    )


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
    meta = get_vista_meta(ctx)
    cfg = meta.user
    cluster = _resolve_cluster(cluster, cfg, job_id)

    if cluster == "perlmutter":
        return await _get_perlmutter_job_status(cfg, job_id)
    # Odo/Frontier cache the log file to disk for the 30s TTL; need the per-agent
    # output dir from project_paths to know where to land it.
    host_output_dir = Path(meta.project_paths.require_output_dir())
    return await _get_olcf_job_status(cfg, host_output_dir, job_id, cluster=cluster)


async def _get_perlmutter_job_status(cfg: UserConfig, job_id: str) -> str:
    iri_client = await create_iri_client(iri_token=cfg.require_nersc_iri_token())
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


async def _get_olcf_job_status(
    cfg: UserConfig, host_output_dir: Path, job_id: str, *, cluster: Cluster,
) -> str:
    """
    Shared Odo/Frontier status: IRI for state, Globus for log fetch + output
    file listing. The two clusters differ only in IRI endpoint and Globus
    collection (see `_create_olcf_iri_for` / `_olcf_collection_id`).

    Each status query transfers the log file once from the cluster's collection
    to the Vista server's local output_dir (overwrites any previous copy) and
    reads its first 200 lines locally. This is meaningfully slower than the old
    SSH `head` (~30s of Globus task overhead per call) but matches the
    "no SSH" architecture choice; see README.
    """
    await _require_olcf_access(cfg, cluster)
    remote_collection = _olcf_collection_id(cluster)
    iri_client = await _create_olcf_iri_for(cluster, cfg)
    status = await iri_client.get_job_status(job_id)
    state = status.get("state", "UNKNOWN").upper()

    metadata = {
        "JOB_ID": job_id,
        "CLUSTER": cluster,
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

    # While the job is still waiting for resources, neither the log file nor the
    # output dir exists — a Globus fetch would just burn ~10-30s of task overhead
    # to learn that. Answer from the IRI state alone.
    if state in _PRE_RUN_STATES:
        return "\n\n".join([
            "\n".join(f"{k}={v}" for k, v in metadata.items()),
            "(job has not started yet; logs and outputs will appear once it runs)",
        ])

    # Pull the log file across Globus, then read locally. The local landing
    # spot doubles as the cached log for subsequent reads — if it was fetched
    # within `_LOG_CACHE_TTL_S`, skip the Globus call entirely. Trades some
    # log freshness for sub-second response on back-to-back status calls in
    # a single chat turn (e.g. status + outputs together).
    logs = "(no logs yet)"
    local_log_path = host_output_dir / job_id / Path(submitted.log_path).name
    local_log_path.parent.mkdir(parents=True, exist_ok=True)
    log_age = (
        time.time() - local_log_path.stat().st_mtime
        if local_log_path.exists() else float("inf")
    )
    if log_age >= _LOG_CACHE_TTL_S:
        try:
            globus = create_globus_client(refresh_token=settings.require_globus_token(cluster))
            await globus.transfer_and_wait(
                src_endpoint=remote_collection,
                dst_endpoint=settings.vista_globus_collection_id,
                items=[(submitted.log_path, str(local_log_path), False)],
                label=f"vista log fetch: {job_id}",
                sync_level="mtime",  # log file grows; mtime is cheaper than checksum
                poll_seconds=3,      # logs are small; don't sit out the default 10s poll
                timeout_seconds=120, # fail fast instead of hanging the chat turn
            )
        except Exception as e:
            logs = f"(unable to fetch logs: {e})"
    else:
        logging.debug(f"serving log from local cache ({log_age:.1f}s old, ttl={_LOG_CACHE_TTL_S}s)")
    if local_log_path.exists():
        try:
            with open(local_log_path) as f:
                lines = []
                for i, line in enumerate(f):
                    if i >= 200:
                        break
                    lines.append(line)
                logs = "".join(lines)
        except Exception as e:
            logs = f"(unable to read cached log: {e})"

    # List the output dir on the cluster's collection via Globus operation_ls
    # (recursive BFS-walk; see lib/globus.py). Drop venv/pycache noise.
    files: list[str] = []
    if submitted.output_dir:
        excludes = (".venv", "__pycache__")
        try:
            ls_globus = create_globus_client(refresh_token=settings.require_globus_token(cluster))
            entries = await ls_globus.operation_ls(
                endpoint=remote_collection,
                path=submitted.output_dir,
                recursive=True,
            )
            for e in entries:
                if e.get("type") != "file":
                    continue
                p = e.get("path", "")
                if any(seg in p for seg in excludes):
                    continue
                rel = p[len(submitted.output_dir):].lstrip("/")
                files.append(rel)
            files = files[:20]
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
    meta = get_vista_meta(ctx)
    cfg = meta.user
    host_output_dir = Path(meta.project_paths.require_output_dir())
    cluster = _resolve_cluster(cluster, cfg, job_id)

    if cluster == "perlmutter":
        return await _get_perlmutter_job_outputs(cfg, host_output_dir, job_id, files)
    return await _get_olcf_job_outputs(cfg, host_output_dir, job_id, files, cluster=cluster)


async def _get_perlmutter_job_outputs(cfg: UserConfig, host_output_dir: Path, job_id: str, files: list[str]) -> str:
    # IRI filesystem download is currently text-only; binary checkpoints are not supported here.
    submitted = _submitted_jobs.get(job_id)
    if submitted is None or submitted.output_dir is None:
        raise ValueError(
            f"No output directory cached for job {job_id!r}. Output retrieval is only "
            f"available for Perlmutter jobs submitted in the current session."
        )

    iri_client = await create_iri_client(iri_token=cfg.require_nersc_iri_token())
    local_out_dir = host_output_dir / job_id
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


async def _get_olcf_job_outputs(
    cfg: UserConfig, host_output_dir: Path, job_id: str, files: list[str], *, cluster: Cluster,
) -> str:
    """
    Shared Odo/Frontier output retrieval via a single Globus transfer task
    (cluster collection → Vista's GCS). Binary files (.pt checkpoints etc.)
    work natively.

    Files already present locally under host_output_dir/<job_id>/ are NOT
    re-fetched — Globus has multi-second per-task overhead and would otherwise
    blow past the UI's /api/chat timeout for follow-up `display_file` calls.
    To force a fresh pull (e.g. checkpoint updated mid-training), delete the
    local copy first.
    """
    await _require_olcf_access(cfg, cluster)
    submitted = _submitted_jobs.get(job_id)
    if submitted is None or submitted.output_dir is None:
        raise ValueError(
            f"No output directory cached for job {job_id!r}. Output retrieval is only "
            f"available for jobs submitted in the current session."
        )

    local_out_dir = host_output_dir / job_id
    sandbox_out_dir = Path("/mnt/data/output") / job_id
    remote_out_dir = submitted.output_dir.rstrip("/")

    items: list[tuple[str, str, bool]] = []
    sandbox_paths: list[str] = []
    cached_paths: list[str] = []
    for file in files:
        # Globus runs as Vista's shared OLCF identity, so this relative-path check + the
        # server-defined remote_out_dir confine the transfer to the resolved output dir.
        # NOTE: users can still request jobs from other users by job id. But since we are limiting
        # access to only gen150-vista and chm245 the jobs all run as a service account the users
        # would have had access to anyways. When OLCF supports IRI file transfer, we can remove
        # globus and rely on the S3M token for restricting file access.
        if ".." in Path(file).parts or Path(file).is_absolute():
            raise ValueError(f'Invalid path "{file}"')
        local_path = local_out_dir / file
        sandbox_path = sandbox_out_dir / file
        sandbox_paths.append(str(sandbox_path))
        if local_path.exists() and local_path.stat().st_size > 0:
            cached_paths.append(str(sandbox_path))
            continue
        remote_path = f"{remote_out_dir}/{file}"
        # Globus only creates the LEAF file via transfer — ensure local parent dirs exist.
        local_path.parent.mkdir(parents=True, exist_ok=True)
        items.append((remote_path, str(local_path), False))

    if items:
        globus = create_globus_client(refresh_token=settings.require_globus_token(cluster))
        await globus.transfer_and_wait(
            src_endpoint=_olcf_collection_id(cluster),
            dst_endpoint=settings.vista_globus_collection_id,
            items=items,
            label=f"vista output download: {job_id}",
        )
        logging.info(f"Globus-fetched {len(items)} file(s); served {len(cached_paths)} from local cache")
    else:
        logging.info(f"All {len(cached_paths)} requested files served from local cache (no Globus call)")

    return "Downloaded files:\n" + "\n".join(sandbox_paths)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
async def list_hpc_jobs(ctx: Context, cluster: Cluster | None = None) -> str:
    """
    List HPC jobs known to this server (persisted across restarts).

    Args:
        cluster: Which cluster to list jobs for. If omitted, lists from the only-configured cluster.
    """
    cfg = get_vista_meta(ctx).user
    cluster = _resolve_cluster(cluster, cfg)
    # None of the IRI services expose user-job listing, so this is the server's
    # registry of jobs submitted via this MCP server. The registry is persisted to
    # disk and reloaded on startup, so this also covers jobs from before a restart.
    # (The old Odo path ran sacct over SSH; that went away with the SSH conn.)
    ids = [jid for jid, s in _submitted_jobs.items() if s.cluster == cluster]
    if not ids:
        return f"No {cluster} jobs known to this server."
    return "\n".join(ids)


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
    cfg = get_vista_meta(ctx).user
    cluster = _resolve_cluster(cluster, cfg, job_id)
    if cluster == "perlmutter":
        iri_client = await create_iri_client(iri_token=cfg.require_nersc_iri_token())
    else:  # "odo" / "frontier"
        iri_client = await _create_olcf_iri_for(cluster, cfg)
    await iri_client.cancel_job(job_id)
    logging.info(f"Cancelled job {job_id} on {cluster}")
    return f"Cancellation requested for job {job_id} on {cluster}."
