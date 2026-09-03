"""
MCP for remote HPC job submission. Dispatches between:
- Odo (OLCF, open enclave) via the AmSC IRI API (`lib/iri.py`); output comes back
  by an S3 push from the compute node (`jobscripts/s3_put.py` + `lib/s3.py`)
- Frontier (OLCF, moderate enclave) via the AmSC IRI API + the same S3 push
- Perlmutter (NERSC) via the NERSC IRI API and amscrot SDK (`lib/iri.py`)
"""
from __future__ import annotations
import base64, json, logging, os, shlex, textwrap, dataclasses
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
from .lib.s3 import create_s3_client
from .lib.olcf_token import require_s3m_project
from .lib.user_config import UserConfig, get_vista_meta
from .lib.misc import parse_time_limit, validate_job_id
from .metrics import stage as metrics_stage
from . import dry_run, faults


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

_PRE_RUN_STATES = {"NEW", "QUEUED", "PENDING", "HELD"}
"""
IRI/PSI-J job states in which the job hasn't touched a compute node yet, so
nothing can have been pushed to S3 — status queries answer from the IRI state
alone while the job is in one of these.
"""

_LOG_TAIL_BYTES = 256 * 1024
""" How much of a pushed job log to fetch before trimming to `_LOG_MAX_LINES`. """

_LOG_MAX_LINES = 200
""" Lines of job log included in a `get_hpc_job_status` response. """


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
    - `s3m_token` is the backend's current generic OLCF token field; it
      supports explicit `cluster="odo"` / `cluster="frontier"` calls but
      does not by itself disambiguate which OLCF cluster to prefer.
    - `nersc_iri_token` enables Perlmutter.
    """
    configured_set: set[Cluster] = set()
    if cfg.odo_s3m_token:
        configured_set.add("odo")
    if cfg.frontier_s3m_token:
        configured_set.add("frontier")
    if cfg.s3m_token:
        configured_set.update({"odo", "frontier"})
    if cfg.nersc_iri_token:
        configured_set.add("perlmutter")
    configured = sorted(configured_set)

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
                IRI service (compute) and push their output to S3, each with its own
                per-enclave S3M token; Perlmutter uses NERSC IRI for both.
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

    # M7 fault injection (inert unless VISTA_MCP_FAULT__* is set): a synthetic
    # submit failure or an expired credential, surfaced as a tool error for
    # the recovery path under test (E7a).
    faults.check_token_expiry()
    faults.maybe_fail_submit()

    # M3 stage timing: the full submission round-trip (source upload + IRI
    # submit) — the platform-attributable part of E3. Facility queue wait
    # is observed separately via status polls (stage.hpc.status).
    # Record + persist so status/outputs survive an MCP-server restart (see _persist_submitted_jobs).
    with metrics_stage("hpc.submit", tool_name="submit_hpc_job",
                       payload={"cluster": cluster, "job": job}):
        if dry_run.enabled():
            # M6 dry-run: synthetic submission, no S3M/IRI/S3 contact.
            eff_nodes = node_count or 1
            eff_duration = duration_int or 3600
            job_id = dry_run.record_submit(cluster, job, eff_nodes, eff_duration)
            _record_submitted_job(job_id, SubmittedJob(cluster=cluster))
        else:
            if cluster == "perlmutter":
                submitted = await _submit_perlmutter_job(
                    cfg, job, node_count, duration_int, script_args,
                )
            else:  # "odo" / "frontier" — one OLCF path, see `_olcf_dispatch`
                submitted = await _submit_olcf_job(
                    cfg, job, node_count, duration_int, script_args, cluster=cluster,
                )
            job_id, log_path, output_dir, eff_nodes, eff_duration = submitted
            _record_submitted_job(
                job_id, SubmittedJob(cluster=cluster, log_path=log_path, output_dir=output_dir),
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


# --- OLCF staging + output push ------------------------------------------
#
# Odo and Frontier have no usable file-transfer channel: the AmSC IRI tokens
# carry no storage scope, and Globus (the previous answer) turned out to be
# unusable unattended because both OLCF collections are High Assurance with a
# 3-day authentication timeout that a token refresh cannot reset. So the data
# moves in the other direction: job sources are *inlined* into the JobSpec, and
# the job *pushes* its own output to S3 on the way out. See
# openspec/changes/replace-globus-with-s3-push/design.md.

_B64_HEREDOC_WIDTH = 76
""" Column width for base64 payloads inlined into the JobSpec. """


def _b64_write_file(content: bytes, dest: str, *, expand: bool = False) -> str:
    """
    Shell that materializes `content` at `dest` via a base64 heredoc.

    Writes to a PID-suffixed temp file and `mv`s it into place: the IRI service
    may run `pre_launch` on more than one node, and an atomic rename keeps
    concurrent writers from serving a half-written file to the job.

    `expand=True` double-quotes the destination so shell variables in it are
    expanded — needed for paths under `$VISTA_SCRATCH`, which is only known at
    runtime. The default single-quotes it, which is what a literal path
    resolved here in Python wants.
    """
    payload = base64.b64encode(content).decode("ascii")
    wrapped = "\n".join(textwrap.wrap(payload, _B64_HEREDOC_WIDTH)) or ""
    quoted = f'"{dest}"' if expand else shlex.quote(dest)
    return (
        f"base64 -d > {quoted}.$$ <<'VISTA_B64_EOF'\n"
        f"{wrapped}\n"
        f"VISTA_B64_EOF\n"
        f"mv -f {quoted}.$$ {quoted}"
    )


def _stage_sources_snippet(job: str, run_dir: str) -> str:
    """
    Shell that recreates `hpc_jobs/<job>/`'s supporting files in `run_dir`.

    Inlined rather than transferred: the whole catalog stages ~47 KB across 6
    files, so embedding them costs nothing and removes the need for any
    cluster-side read credential. The excluded set matches what the Perlmutter
    IRI uploader skips (`_HPC_JOB_METADATA_FILES`, dotfiles, subdirectories) —
    job scripts and setup scripts are inlined into the JobSpec separately.

    This runs in `pre_launch`, not the job body, because `setup_<cluster>.sh`
    validates that these files exist and runs before the body.
    """
    local_job_dir = settings.local_hpc_jobs_dir / job
    blocks = [f"mkdir -p {shlex.quote(run_dir)}"]
    staged: list[str] = []
    for f in sorted(local_job_dir.iterdir()):
        if not f.is_file() or f.name.startswith(".") or f.name in _HPC_JOB_METADATA_FILES:
            continue
        blocks.append(_b64_write_file(f.read_bytes(), f"{run_dir}/{f.name}"))
        staged.append(f.name)
    if staged:
        logging.info(f"Inlining {len(staged)} source file(s) for {job}: {staged}")
    else:
        logging.warning(f"no source files to inline from {local_job_dir} (only metadata?)")
    return "\n".join(blocks)


def _output_push_snippet(*, out_dir: str, cluster: Cluster) -> str:
    """
    Shell that exports the S3 push contract and installs the exit trap.

    A trap rather than a suffix appended after the job body: job scripts `exit`
    and run under `set -e`, and a failed job is exactly when its log is worth
    having. EXIT also covers Slurm's SIGTERM at the time limit. The trap
    returns the status that triggered it, so pushing output cannot mask the
    job's own exit code.

    The key prefix is built through `job_key_prefix` with `$SLURM_JOB_ID` left
    for the shell to expand, because the job id isn't known until `submit_job`
    returns, after this snippet is baked into the JobSpec. That relies on the
    IRI job id being the Slurm job id — the same assumption
    `stdout_template.replace("%j", job_id)` already makes. Routing it through
    the same helper the read path uses keeps the two sides of the layout
    (including the cluster component) from drifting apart.

    The trap resolves an interpreter defensively: job bodies `module purge` and
    load their own toolchains, so it cannot assume the body left a `python3` on
    PATH.
    """
    bucket, key_id, secret = settings.require_job_credentials()
    exports = [
        f"export VISTA_S3_BUCKET={shlex.quote(bucket)}",
        f"export VISTA_S3_REGION={shlex.quote(settings.s3.region)}",
        f'export VISTA_S3_PREFIX="{settings.job_key_prefix(cluster, "$SLURM_JOB_ID")}"',
        f"export VISTA_S3_KEY_ID={shlex.quote(key_id)}",
        f"export VISTA_S3_SECRET={shlex.quote(secret)}",
        f'export VISTA_LOG="{out_dir}/log-$SLURM_JOB_ID.out"',
        f'export VISTA_LOG_ERR="{out_dir}/log-$SLURM_JOB_ID.err"',
    ]
    if settings.s3.endpoint:
        exports.append(f"export VISTA_S3_ENDPOINT={shlex.quote(settings.s3.endpoint)}")
    pusher_src = (settings.jobscripts_dir / "s3_put.py").read_bytes()
    write_pusher = _b64_write_file(
        pusher_src, "$VISTA_SCRATCH/.vista/s3_put.py", expand=True,
    )
    trap = textwrap.dedent("""
        _vista_push_outputs() {
            _vista_status=$?
            _vista_py="$(command -v python3 || command -v python || true)"
            if [ -z "$_vista_py" ]; then
                module load cray-python 2>/dev/null || module load python 2>/dev/null || true
                _vista_py="$(command -v python3 || command -v python || true)"
            fi
            if [ -n "$_vista_py" ]; then
                "$_vista_py" "$VISTA_SCRATCH/.vista/s3_put.py" \\
                    || echo "[vista] output push reported errors" >&2
            else
                echo "[vista] no python3 on the compute node; output was NOT pushed" >&2
            fi
            if [ -n "${VISTA_SCRATCH:-}" ]; then rm -rf "$VISTA_SCRATCH"; fi
            return $_vista_status
        }
        trap _vista_push_outputs EXIT
    """).strip()
    return "\n".join([*exports, write_pusher, "", trap])


def _olcf_job_cmd(setup_snippet: str, script_args: str | None, job_script_text: str) -> str:
    """
    Assemble an OLCF job body: prefix, positional args, then the job script
    **inside a subshell**.

    The subshell is load-bearing, not cosmetic. A job script that installs its
    own `trap ... EXIT` would otherwise replace the output-push trap (which
    `hpc_jobs/salt-neutronics-tbr/job.odo.slurm` does), and one that `exec`s
    would replace the shell outright — in both cases the push would silently
    never run and the job's results would be stranded on the cluster. Running
    the body as a subshell confines its traps and any `exec` to a child, while
    still propagating its exit status to the parent (and thus to Slurm).
    """
    parts = [setup_snippet]
    job_cmd_args = shlex.join(shlex.split(script_args or ""))
    if job_cmd_args:
        parts.append(f"set -- {job_cmd_args}")
    parts += ["(", job_script_text, ")"]
    return "\n".join(parts) + "\n"


def _olcf_pre_launch(job: str, src_dir: str, setup_script_path: Path) -> str:
    """
    Build the IRI `pre_launch` attribute for an OLCF job.

    `pre_launch` runs on the compute node *before* the job body, and every
    `setup_<cluster>.sh` in the catalog validates that the job's supporting
    files are present in `RUN_DIR_<Cluster>`. Since those files are now inlined
    rather than transferred ahead of submission, they have to be materialized
    here — the body's setup snippet would run too late for that check.
    """
    parts = [_stage_sources_snippet(job, src_dir)]
    if setup_script_path.exists():
        parts.append(setup_script_path.read_text())
    return f"bash -lc {shlex.quote(chr(10).join(parts))}"


def _olcf_setup_snippet(
    *, cluster: Cluster, out_dir: str, scratch_root: str, keep_root: str, run_dir: str,
    cd_into_run_dir: bool = False,
) -> str:
    """
    The shell prefix prepended to every OLCF job body.

    Exports the three-directory output contract, the OLCF proxy (compute nodes
    have no direct outbound network, so pip / curl / git need it), a `HOME`
    fallback, and the S3 push trap; then optionally `cd`s into the staged
    source dir.

    `$VISTA_OUT` is uploaded verbatim, so `HOME` must default to
    `$VISTA_SCRATCH` — pointing it at the output dir would upload every dotfile
    pip and matplotlib leave behind. `module purge` clears modules leaking in
    from the IRI service's host env via `--export=ALL`, which otherwise stacks
    Cray PE modules and segfaults PyTorch at import.
    """
    lines = textwrap.dedent(f"""
        export VISTA_OUT="{out_dir}/$SLURM_JOB_ID"
        export VISTA_SCRATCH="{scratch_root}/$SLURM_JOB_ID"
        export VISTA_KEEP="{keep_root}/$SLURM_JOB_ID"
        mkdir -p "$VISTA_OUT" "$VISTA_SCRATCH/.vista" "$VISTA_KEEP"

        # $VISTA_OUT is pushed to S3 verbatim on exit; working files (venvs,
        # clones, caches, HOME) belong in $VISTA_SCRATCH, which is not pushed
        # and is deleted afterwards. $VISTA_KEEP is for files too large to be
        # worth uploading that a later job still needs on the cluster (model
        # checkpoints): kept, never pushed, never retrievable through
        # get_hpc_job_outputs.
        export HOME="${{HOME:-$VISTA_SCRATCH}}"

        export https_proxy="http://proxy.ccs.ornl.gov:3128"
        export http_proxy="http://proxy.ccs.ornl.gov:3128"
        export no_proxy="localhost,127.0.0.1,0.0.0.0"

        module purge 2>/dev/null || true
    """).strip()
    parts = [lines, "", _output_push_snippet(out_dir=out_dir, cluster=cluster)]
    if cd_into_run_dir:
        parts += ["", f'cd "{run_dir}"']
    return "\n".join(parts)


@dataclasses.dataclass(frozen=True)
class _OlcfDispatch:
    """
    The per-cluster half of an OLCF submission — everything `_submit_olcf_job`
    cannot derive from the cluster name alone.

    Built per call by `_olcf_dispatch` rather than held in a module-level table,
    so settings changes (and the monkeypatched settings in tests) are picked up.
    """
    job_script: str
    setup_script: str
    remote_dir: str
    account: str
    machine: str
    image_var: str
    module_var: str
    cd_into_run_dir: bool
    start_in_session_dir: bool


def _olcf_dispatch(cluster: Cluster) -> _OlcfDispatch:
    """ Resolve the per-cluster values for `cluster` ("odo" or "frontier"). """
    if cluster == "odo":
        return _OlcfDispatch(
            job_script=ODO_JOB_SCRIPT,
            setup_script=ODO_SETUP_SCRIPT,
            remote_dir=settings.odo_remote_dir,
            account=settings.odo_account,
            machine=settings.odo_machine,
            image_var="VISTA_ODO_IMAGE",
            module_var="VISTA_ODO_MODULE",
            # job.odo.slurm scripts reference example.py / forge-tune.py relative
            # to the working directory, with no RUN_DIR_ guard, so the setup
            # snippet has to `cd` for them.
            cd_into_run_dir=True,
            # Odo starts in the preset remote base. Its `cd` above makes the
            # initial working directory moot either way.
            start_in_session_dir=False,
        )
    return _OlcfDispatch(
        job_script=FRONTIER_JOB_SCRIPT,
        setup_script=FRONTIER_SETUP_SCRIPT,
        remote_dir=settings.frontier_remote_dir,
        account=settings.frontier_account,
        machine=settings.frontier_machine,
        image_var="VISTA_FR_IMAGE",
        module_var="VISTA_FR_MODULE",
        # Unlike Odo, no `cd`: job.frontier.slurm references RUN_DIR_Frontier
        # explicitly.
        cd_into_run_dir=False,
        start_in_session_dir=True,
    )


async def _submit_olcf_job(
    cfg: UserConfig, job: str, node_count: int | None, duration_int: int | None,
    script_args: str | None, *, cluster: Cluster,
) -> tuple[str, str, str, int, int]:
    """
    Odo (open enclave) and Frontier (moderate enclave) dispatch.

    One path for both: IRI for compute, an S3 push for output. Neither
    enclave's S3M token authorizes IRI storage discovery, so there is no file
    channel in either direction — job sources are inlined into the JobSpec and
    the job pushes its own output to S3 on exit (`_olcf_setup_snippet`). The
    Slurm account is a global per-cluster setting (one shared OLCF project for
    all Vista users), and the user's S3M token must belong to it. Everything
    that does differ between the two lives in `_olcf_dispatch`.

    Every directory is created by the job itself, all as the IRI automation
    user, so there is no ownership or mode juggling to do: slurmstepd `mkdir
    -p`s the stdio path's parents, and it does so *before* chdir, so writing
    the log under `session_dir` is also what makes `session_dir` exist for
    `directory` below. (Verified on Odo: `sbatch -D ./new -o ./new/out/x.log`
    runs, and the relative -o resolves against -D.) The setup snippet mkdirs
    the rest. This is why the Globus-era `operation_mkdir_p` needed no
    replacement, and why the old group-writable/pre-created-`out` dance — which
    existed only because Globus created those dirs as a different account —
    could go.

    Returns (job_id, rendered_log_path, rendered_output_dir, effective_node_count, effective_duration_seconds).
    """
    d = _olcf_dispatch(cluster)
    name = cluster.capitalize()
    defaults = getattr(AVAILABLE_JOBS[job].cluster_defaults, cluster)
    if defaults is None:
        raise ValueError(f"Job '{job}' has no \"{cluster}\" section in cluster_defaults.json")

    local_job_dir = settings.local_hpc_jobs_dir / job
    job_script_path = local_job_dir / d.job_script
    if not job_script_path.exists():
        raise ValueError(
            f"Job '{job}' has no {name} script at {job_script_path}. "
            f"Add a {d.job_script} to enable {name} submission."
        )

    await _require_olcf_access(cfg, cluster)
    iri_client = await _create_olcf_iri_for(cluster, cfg)
    base = d.remote_dir.rstrip('/')
    session_dir = f"{base}/{settings.session_id}"
    out_dir = f"{session_dir}/out"
    src_dir = f"{session_dir}/src/{job}"
    scratch_root = f"{session_dir}/scratch"
    # Deliberately NOT under session_dir: `session_id` changes on every MCP
    # server restart, and the whole point of $VISTA_KEEP is that a later job can
    # still find yesterday's checkpoint to `--resume-from`. Nothing prunes it —
    # both remote dirs live on a `proj-shared` filesystem whose facility purge
    # policy is the retention story.
    keep_root = f"{base}/keep"

    job_script_text = job_script_path.read_text()
    pre_launch = _olcf_pre_launch(job, src_dir, local_job_dir / d.setup_script)

    nodes = node_count or defaults.resources.node_count or 1
    workers_per_node = defaults.resources.processes_per_node or 1
    duration = duration_int or defaults.duration

    # See `_olcf_setup_snippet` for the HOME / proxy / module purge rationale
    # (amscrot's IRI env carries no HOME, and a missing one silently breaks the
    # conda hooks `module load xforge` installs).
    setup_snippet = _olcf_setup_snippet(
        cluster=cluster, out_dir=out_dir, scratch_root=scratch_root, keep_root=keep_root,
        run_dir=src_dir, cd_into_run_dir=d.cd_into_run_dir,
    )
    job_cmd = _olcf_job_cmd(setup_snippet, script_args, job_script_text)

    # Cluster-suffixed env vars so a job script can pull them in without
    # colliding with another cluster's (RUN_DIR_Odo / RUN_DIR_Frontier /
    # RUN_DIR_Perlmutter).
    iri_env = {
        f"RUN_DIR_{name}": src_dir,
        f"FORGE_MODEL_{name}": f"{base}/{job}/model",
    }
    iri_env.update(defaults.iri.environment)  # user-supplied JSON entries win

    if defaults.iri.image is not None:
        iri_env[d.image_var] = defaults.iri.image
    if defaults.iri.module is not None:
        iri_env[d.module_var] = defaults.iri.module
    # NOTE: we do NOT set SLURM_GPUS_PER_NODE here. On Frontier with `--exclusive`
    # the prolog already binds all 8 GCDs and exports SLURM_GPUS_ON_NODE=8 plus
    # SLURM_JOB_GPUS=0..7 automatically — setting SLURM_GPUS_PER_NODE is redundant.
    # (Perlmutter's dispatch sets it because shifter reads it for per-task GCD
    # binding inside the container; neither OLCF cluster uses shifter here.)

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
            "account": d.account,
            "duration": duration,
            **({"custom_attributes": {"constraint": defaults.iri.constraint}} if defaults.iri.constraint else {}),
            **({"pre_launch": pre_launch} if pre_launch else {}),
            "directory": session_dir if d.start_in_session_dir else base,
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
    logging.info(f"Submitted job {job_id} via IRI to {d.machine}")
    return job_id, stdout_template.replace("%j", job_id), f"{out_dir}/{job_id}", nodes, duration

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
    # `VISTA_SCRATCH` and `VISTA_KEEP` are exported here too even though
    # Perlmutter's output comes back over the IRI filesystem API rather than an
    # S3 push: job scripts are shared across clusters, so the three-directory
    # contract has to be uniform. `keep` sits outside the session dir for the
    # same reason it does on OLCF (see `_submit_olcf_job`).
    setup_snippet = textwrap.dedent(f"""
        export VISTA_OUT="{out_dir}/$SLURM_JOB_ID"
        export VISTA_SCRATCH="{session_dir}/scratch/$SLURM_JOB_ID"
        export VISTA_KEEP="{base}/keep/$SLURM_JOB_ID"
        mkdir -p "$VISTA_OUT" "$VISTA_SCRATCH" "$VISTA_KEEP"
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
    # Perlmutter is GPU-first: surface GPUs-per-node so GPU job scripts (e.g. shifter)
    # can read it. Skip it for CPU-partition jobs (constraint="cpu"); otherwise Slurm
    # requests a gpu gres the CPU node can't satisfy and the step launch fails with
    # "Invalid generic resource (gres) specification" before the job script runs.
    if defaults.iri.constraint != "cpu":
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


async def _create_olcf_iri_for(cluster: Cluster, cfg: UserConfig) -> IriClient:
    """ IRI client for an OLCF cluster: "odo" (open enclave) or "frontier" (moderate). """
    if cluster == "odo":
        return await create_odo_iri_client(iri_token=cfg.require_s3m_token("odo"))
    return await create_olcf_iri_client(iri_token=cfg.require_s3m_token("frontier"))


async def _require_olcf_access(cfg: UserConfig, cluster: Cluster) -> None:
    """
    Verify the user's S3M token belongs to the cluster's OLCF project before
    any job data is served. Job output lives in Vista's own S3 bucket, written
    by jobs that all run under one shared project service account, so this
    introspection is what authorizes the user — it must guard every read path,
    including `_get_olcf_job_outputs`, which never calls IRI.
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

    # M7 fault injection (inert unless VISTA_MCP_FAULT__* is set): a poll
    # timeout or an expired credential mid-campaign (E7a / E7b 24h case).
    faults.check_token_expiry()
    faults.maybe_timeout_status()

    # M3 stage timing: status-poll round-trip per cluster (E3/E7b polling
    # overhead).
    with metrics_stage("hpc.status", tool_name="get_hpc_job_status",
                       payload={"cluster": cluster}):
        if dry_run.is_dry_job(job_id):
            return dry_run.status_text(job_id)
        if cluster == "perlmutter":
            return await _get_perlmutter_job_status(cfg, job_id)
        return await _get_olcf_job_status(cfg, job_id, cluster=cluster)


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


async def _get_olcf_job_status(cfg: UserConfig, job_id: str, *, cluster: Cluster) -> str:
    """
    Shared Odo/Frontier status: IRI for state, S3 for the pushed log and output
    listing. The two clusters differ only in IRI endpoint
    (`_create_olcf_iri_for`); the S3 layout is identical.

    Logs appear only once the job exits, because the push runs from the job's
    exit trap. While a job is running, the IRI state is the whole answer — the
    same trade the previous Globus-based partial-log fetch avoided, at the cost
    of a credential that expired every three days.

    Needs no entry in `_submitted_jobs`: every key is derived from the cluster
    and job id, so status works for jobs submitted before a server restart.
    """
    await _require_olcf_access(cfg, cluster)
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

    def render(*sections: str) -> str:
        return "\n\n".join(["\n".join(f"{k}={v}" for k, v in metadata.items()), *sections])

    # Nothing has been pushed while the job is still waiting for resources.
    if state in _PRE_RUN_STATES:
        return render("(job has not started yet; logs and outputs are uploaded when it finishes)")

    s3 = create_s3_client()
    key_prefix = settings.job_key_prefix(cluster, job_id)

    logs = "(no logs yet — the job uploads them when it exits)"
    try:
        text = await s3.get_text(key=f"{key_prefix}/log.out", max_bytes=_LOG_TAIL_BYTES)
        lines = text.splitlines()[:_LOG_MAX_LINES]
        # An uploaded-but-empty log is a different answer from a log that has
        # not arrived: the job wrote nothing to stdout (all of it on stderr, or
        # killed before its first line). Saying "not yet" for a finished job
        # sends the agent looking for an upload that already happened.
        logs = "\n".join(lines) if lines else "(the job's stdout log is empty)"
    except Exception as e:
        logging.debug(f"job log not in S3 yet ({key_prefix}/log.out): {e}")

    files: list[str] = []
    try:
        entries = await s3.list_objects(prefix=f"{key_prefix}/out")
        files = [e["path"] for e in entries][:20]
    except Exception as e:
        logging.info(f"output listing not available yet ({key_prefix}/out): {e}")

    sections = [
        "--- LOGS ---",
        logs.strip() if logs.strip() else "(no logs yet)",
        "--- OUTPUT FILES ---",
        "\n".join(files) if files else "(no output files yet)",
    ]

    # The manifest is written last, so a terminal job without one means the
    # push was cut short — a different problem from a job that produced
    # nothing, and the agent must not report it as the latter.
    if state not in _PRE_RUN_STATES and state not in {"ACTIVE", "RUNNING"}:
        try:
            if not await s3.object_exists(key=f"{key_prefix}/manifest.json"):
                sections.append(
                    "--- WARNING ---\n"
                    "This job is finished but its output upload did not complete "
                    "(no manifest). Any files listed above are partial; check the "
                    "job log for '[vista-s3]' errors."
                )
        except Exception as e:
            logging.info(f"could not check upload manifest for {job_id}: {e}")

    return render(*sections)


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
    Shared Odo/Frontier output retrieval, reading the tree the job pushed to S3.
    Binary files (.pt checkpoints etc.) work natively — unlike the IRI
    filesystem API's text-only `download`, which is why OLCF needed a separate
    transfer path in the first place.

    Files already present locally under host_output_dir/<job_id>/ are NOT
    re-fetched, so a follow-up `display_file` on the same object costs nothing.
    To force a fresh pull (e.g. a checkpoint updated by a later job), delete the
    local copy first.

    Needs no entry in `_submitted_jobs`: keys derive from the cluster and job
    id, so retrieval survives a server restart.
    """
    await _require_olcf_access(cfg, cluster)
    s3 = create_s3_client()
    key_prefix = f"{settings.job_key_prefix(cluster, job_id)}/out"

    local_out_dir = host_output_dir / job_id
    sandbox_out_dir = Path("/mnt/data/output") / job_id

    wanted: list[tuple[str, Path]] = []
    sandbox_paths: list[str] = []
    cached = 0
    for file in files:
        # The relative-path check plus the server-derived key prefix confine
        # reads to this job's uploaded tree — and because the prefix carries the
        # cluster, a caller cannot reach a Frontier job's output by passing its
        # id with `cluster="odo"`.
        # NOTE: users can still request another user's job by id. Every job runs
        # under one shared project service account, so those files were already
        # reachable by any member of the project; per-user isolation is out of
        # scope for the beta.
        if ".." in Path(file).parts or Path(file).is_absolute():
            raise ValueError(f'Invalid path "{file}"')
        local_path = local_out_dir / file
        sandbox_paths.append(str(sandbox_out_dir / file))
        if local_path.exists() and local_path.stat().st_size > 0:
            cached += 1
            continue
        wanted.append((f"{key_prefix}/{file}", local_path))

    for key, local_path in wanted:
        await s3.download_file(key=key, local_path=local_path)

    if wanted:
        logging.info(f"Downloaded {len(wanted)} object(s) from S3; served {cached} from local cache")
    else:
        logging.info(f"All {cached} requested files served from local cache (no S3 call)")

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
    if dry_run.is_dry_job(job_id):
        return dry_run.cancel(job_id)
    cfg = get_vista_meta(ctx).user
    cluster = _resolve_cluster(cluster, cfg, job_id)
    if cluster == "perlmutter":
        iri_client = await create_iri_client(iri_token=cfg.require_nersc_iri_token())
    else:  # "odo" / "frontier"
        iri_client = await _create_olcf_iri_for(cluster, cfg)
    await iri_client.cancel_job(job_id)
    logging.info(f"Cancelled job {job_id} on {cluster}")
    return f"Cancellation requested for job {job_id} on {cluster}."
