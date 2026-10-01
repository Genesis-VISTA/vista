"""
MCP for remote HPC job submission. Dispatches between:
- Odo (OLCF, open enclave) via the AmSC IRI API (`lib/iri.py`) + Globus file ops (`lib/globus.py`)
- Frontier (OLCF, moderate enclave) via the AmSC IRI API + Globus file ops
- Perlmutter (NERSC) via the NERSC IRI API and amscrot SDK (`lib/iri.py`)
- Lux (OLCF) via `sbatch` over SSH (`lib/slurm_ssh.py`) -- it has no IRI service. The
  researcher logs in through the hub with their own passcodes, once per chat session.

The OLCF file ops are HTTPS `GET`/`PUT` straight against the cluster's own
Globus collection, with directory listing and `mkdir` still on the Transfer API
(see `lib/globus.py`). VISTA runs no Globus endpoint of its own, so there is no
second collection to own, to start, or to have been created by the wrong
identity.
"""
from __future__ import annotations
import logging, os, posixpath, shlex, textwrap, dataclasses
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal, NoReturn

from fastmcp import FastMCP, Context
from fastmcp.exceptions import ToolError
from pydantic import BaseModel
from mcp.types import ToolAnnotations

from .config import settings
from .lib.iri import (
    IriClient, IriDefaults, create_iri_client, create_odo_iri_client, create_olcf_iri_client,
)
from .lib.globus import (
    GlobusClient, GlobusFileNotFound, GlobusSessionExpired, create_globus_client,
)
from .lib.olcf_token import get_s3m_token_project
from .lib import slurm_ssh
from .lib.ssh import get_ssh_conn_mcp_elicitation
from .lib.user_config import UserConfig, get_vista_meta
from .lib.misc import get_tool_call_string, parse_time_limit, validate_job_id
from .metrics import stage as metrics_stage
from . import dry_run, faults


Cluster = Literal["odo", "perlmutter", "frontier", "lux"]
""" Supported HPC clusters. """

PERLMUTTER_JOB_SCRIPT = "job.perlmutter.slurm"
PERLMUTTER_SETUP_SCRIPT = "setup_perlmutter.sh"
""" Optional pre_launch setup script in each job dir; inlined into JobSpec.attributes.pre_launch. """

FRONTIER_JOB_SCRIPT = "job.frontier.slurm"
FRONTIER_SETUP_SCRIPT = "setup_frontier.sh"

ODO_JOB_SCRIPT = "job.odo.slurm"
ODO_SETUP_SCRIPT = "setup_odo.sh"

LUX_JOB_SCRIPT = "job.lux.slurm"
LUX_SETUP_SCRIPT = "setup_lux.sh"
""" Unlike the IRI clusters' setup scripts, this runs on the Lux LOGIN node, before sbatch. """

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
    LUX_JOB_SCRIPT,
    LUX_SETUP_SCRIPT,
}


class ClusterDefaults(BaseModel):
    """
    Per-job defaults loaded from `<job>/cluster_defaults.json`. A job opts in to a cluster
    by including the corresponding section.
    """
    odo: IriDefaults | None = None
    perlmutter: IriDefaults | None = None
    frontier: IriDefaults | None = None
    lux: IriDefaults | None = None
    """ Same shape as the IRI sections; rendered into `#SBATCH` directives instead of a JobSpec. """


@dataclasses.dataclass
class JobInfo:
    name: str
    description: str
    cluster_defaults: ClusterDefaults


_CLUSTER_JOB_SCRIPTS = (ODO_JOB_SCRIPT, PERLMUTTER_JOB_SCRIPT, FRONTIER_JOB_SCRIPT, LUX_JOB_SCRIPT)


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
            description = readme.read_text(encoding="utf-8").strip()
            if not description.startswith(f"# {file.name}"):
                raise ValueError(f'Job README.md should start with "# {file.name}" header')
            cluster_defaults_file = file / "cluster_defaults.json"
            if cluster_defaults_file.exists():
                cluster_defaults = ClusterDefaults.model_validate_json(cluster_defaults_file.read_text(encoding="utf-8"))
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

_LOG_TAIL_LINES = 200
"""
How much of an OLCF job log a status query returns -- the LAST this many lines.

The head was what the old whole-file fetch could afford. For a running or
failed job it is module loads and startup noise; what the researcher is looking
for is at the other end. Ranged reads make the other end the cheap one.
"""

_LOG_TAIL_BYTES = 256 * 1024
"""
How much of the accumulated local log to read back off disk to find those
lines. A ceiling on the read, not on the fetch: a job that emits a megabyte
between two polls still has all of it kept locally.
"""

_PRE_RUN_STATES = {"NEW", "QUEUED", "PENDING", "HELD"}
"""
IRI/PSI-J job states in which the job hasn't touched a compute node yet, so no
log file or output dir can exist — status queries skip the Globus round trips
entirely while the job is in one of these.
"""


mcp = FastMCP("Submit Job")


@dataclasses.dataclass(frozen=True)
class RemoteLayout:
    """
    Where a job's files live, given the researcher's remote folder `<base>` for
    a cluster. On the OLCF clusters (Odo, Frontier, Lux), two folders beside
    `<base>`, which itself is never created:

        <base>.<user>.jobs/<job>/src/  sources, uploaded by VISTA; the job only reads them
        <base>.out/log-<id>.out        Slurm stdout, and `.err` beside it
        <base>.out/<id>/               the job's outputs, exported to it as `VISTA_OUT`
        <base>.out/<job>/              state shared by a job's runs (a checkout, a
                                       downloaded model), exported as `VISTA_JOB_DIR`

    On Perlmutter, one folder: `<base>/jobs/...` and `<base>/out/...`, laid out
    the same way inside it (`nested`).

    Every path but the sources is a function of the folder and the job id alone.
    So a job is found again from its id -- by a later status call, after a
    restart, or from another install sharing the folder -- with nothing recorded
    at submit time. Changing the folder setting loses sight of the jobs under the
    old one, which is the price of keeping no record.

    Why OLCF splits them: each folder is created by the only identity that writes
    to it. VISTA uploads sources through the researcher's Globus identity (SFTP
    on Lux), so `.<user>.jobs` belongs to them, mode 755 -- one per researcher,
    so two researchers sharing `<base>` never write into each other's. On Odo and
    Frontier the job runs as the project's IRI automation user, and Slurm creates
    `.out` for the logs as that user, which VISTA cannot do: Globus creates only
    as the researcher and cannot chmod. So `.out` needs only the folder holding
    `<base>` to be writable by the project's group, which OLCF's `proj-shared` is
    (770), and every job prefix makes `.out` itself group-writable (see
    `_shared_out_prefix`), so the automation user, the researcher on Lux, and
    their colleagues can all write there.

    Perlmutter runs as the researcher, and VISTA manages its files through the
    NERSC IRI filesystem API, so nothing there needs splitting.

    TEMPORARY: the OLCF split works around S3M tokens having no access to the IRI
    filesystem API. Once they do, VISTA can create one group-writable folder as
    the automation user (IRI `mkdir` + `chmod`) and use Perlmutter's layout.
    """

    base: str
    nested: bool = False
    """ Perlmutter's one-folder layout (`<base>/jobs`, `<base>/out`). """
    user: str | None = None
    """
    The researcher's POSIX username, naming their sources folder on OLCF. Only
    submission needs it; status and outputs read `.out` alone.
    """

    def with_user(self, user: str) -> RemoteLayout:
        """ This layout, now that the researcher's username is known. """
        return dataclasses.replace(self, user=user)

    @property
    def jobs(self) -> str:
        if self.nested:
            return f"{self.base}/jobs"
        if not self.user:
            raise RuntimeError("the OLCF sources folder needs the researcher's username")
        return f"{self.base}.{self.user}.jobs"

    @property
    def out(self) -> str:
        return f"{self.base}/out" if self.nested else f"{self.base}.out"

    @property
    def parent(self) -> str:
        """ The folder that must already exist: the one holding the sources and `out`. """
        return self.base if self.nested else (posixpath.dirname(self.base) or "/")

    def src(self, job: str) -> str:
        return f"{self.jobs}/{job}/src"

    def job_dir(self, job: str) -> str:
        """ Where a job's runs share state they write, so the job's user can. """
        return f"{self.out}/{job}"

    @property
    def stdout_template(self) -> str:
        """ The Slurm `%j` pattern for stdout; see `log_path` for a job's. """
        return f"{self.out}/log-%j.out"

    @property
    def stderr_template(self) -> str:
        return f"{self.out}/log-%j.err"

    def log_path(self, job_id: str) -> str:
        return self.stdout_template.replace("%j", job_id)

    def err_path(self, job_id: str) -> str:
        """
        The job's stderr file, which is where a failure explains itself.

        Every cluster spec writes one beside stdout, but only stdout used to be
        fetched. A job that died with a Python traceback or an argparse usage
        message therefore looked silent: the `.out` file held the setup script's
        echoes and stopped, and the reason was in a file nothing in Vista knew
        existed. Two debate simulations were reported to an agent as "no outputs
        recorded" that way, both of them argparse rejecting invented flags with
        exit status 2.
        """
        return self.stderr_template.replace("%j", job_id)

    def output_dir(self, job_id: str) -> str:
        return f"{self.out}/{job_id}"


def _layout(cfg: UserConfig, cluster: Cluster) -> RemoteLayout:
    """
    The layout for the researcher's remote folder on `cluster`; see
    `RemoteLayout`. On OLCF, submission adds the username with `with_user`.
    """
    return RemoteLayout(cfg.require_remote_dir(cluster), nested=cluster == "perlmutter")


def _shared_out_prefix(out: str, group: str | None) -> str:
    """
    The first lines of every job: make `out` and everything the job creates in
    it writable by the project's group.

    On OLCF, `<base>.out` is shared by identities that differ -- the project's
    IRI automation user (Odo, Frontier), the researcher (Lux), and colleagues who
    set the same folder -- and is created by whichever runs first. `chgrp` and
    `chmod` succeed only for its owner, which is the identity that made it, so
    the first job fixes it for everyone after; for anyone else they fail
    quietly. Setgid keeps the group on everything created below, and `umask 002`
    keeps that group able to write it.
    """
    lines = ["umask 002"]
    if group:
        lines.append(f"chgrp {shlex.quote(group)} {shlex.quote(out)} 2>/dev/null || true")
    lines.append(f"chmod 2775 {shlex.quote(out)} 2>/dev/null || true")
    return "\n".join(lines)


def _default_cluster(cfg: UserConfig) -> Cluster:
    """
    Return the only configured cluster. Raises if zero or multiple are configured.

    Each S3M token is per-cluster (group-scoped to one OLCF project), so the
    presence of a token directly selects the cluster:

    - `odo_s3m_token` enables Odo.
    - `frontier_s3m_token` enables Frontier.
    - `nersc_iri_token` enables Perlmutter.
    """
    configured_set: set[Cluster] = set()
    if cfg.odo_s3m_token:
        configured_set.add("odo")
    if cfg.frontier_s3m_token:
        configured_set.add("frontier")
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


def _resolve_cluster(cluster: Cluster | None, cfg: UserConfig) -> Cluster:
    """
    Pick the cluster to submit to: the explicit argument, else the only cluster
    the researcher has credentials for. Submission only: a job id says nothing
    about its cluster -- the ids are only unique within one, and Lux, which
    needs no token, is never the fallback -- so status, outputs and cancel
    require the cluster the submit summary named.
    """
    if cluster is not None:
        return cluster
    return _default_cluster(cfg)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True),
    description=textwrap.dedent(f"""
        Submit a job to the HPC system.

        Args:
            job: The name of the job to run (available jobs: {' '.join(AVAILABLE_JOBS.keys())})
            cluster: Which cluster to submit to ("odo", "frontier", "perlmutter", or "lux"). If
                only one cluster is configured, this can be omitted. Odo and Frontier use OLCF's
                IRI service (compute) plus Globus (files), each with its own per-enclave S3M
                token; Perlmutter uses NERSC IRI. Lux has no token and must always be named
                explicitly; it uses SSH, and the user is prompted to log in (once per session).
            node_count: Number of nodes for the job (max: {MAX_NODES})
            duration: Time limit for the job in "h:mm:ss" format (max: {MAX_TIME})
            script_args: Extra arguments to pass to the script

        Returns:
            A multi-line ground-truth summary of the submitted job (job_id, cluster,
            nodes, duration, and where its log and outputs are). Pass the job_id
            and the cluster verbatim to get_hpc_job_status and other follow-up
            tools -- a job id is only unique within its cluster, and nothing else
            remembers which cluster a job went to. Report the rest as-is to the
            user without inventing default values.

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
        "lux": "lux",
    }[cluster]

    # M7 fault injection (inert unless VISTA_MCP_FAULT__* is set): a synthetic
    # submit failure or an expired credential, surfaced as a tool error for
    # the recovery path under test (E7a).
    faults.check_token_expiry()
    faults.maybe_fail_submit()

    # M3 stage timing: the full submission round-trip (source upload + IRI
    # submit) — the platform-attributable part of E3. Facility queue wait
    # is observed separately via status polls (stage.hpc.status).
    with metrics_stage("hpc.submit", tool_name="submit_hpc_job",
                       payload={"cluster": cluster, "job": job}):
        if dry_run.enabled():
            # M6 dry-run: synthetic submission, no S3M/IRI/Globus contact.
            eff_nodes = node_count or 1
            eff_duration = duration_int or 3600
            job_id = dry_run.record_submit(cluster, job, eff_nodes, eff_duration)
        elif cluster == "odo":
            job_id, log_path, err_path, output_dir, eff_nodes, eff_duration = await _submit_odo_job(
                cfg, job, node_count, duration_int, script_args,
            )
        elif cluster == "perlmutter":
            job_id, log_path, err_path, output_dir, eff_nodes, eff_duration = await _submit_perlmutter_job(
                cfg, job, node_count, duration_int, script_args,
            )
        elif cluster == "lux":
            job_id, log_path, err_path, output_dir, eff_nodes, eff_duration = await _submit_lux_job(
                ctx, cfg, job, node_count, duration_int, script_args,
            )
        else:  # "frontier"
            job_id, log_path, err_path, output_dir, eff_nodes, eff_duration = await _submit_frontier_job(
                cfg, job, node_count, duration_int, script_args,
            )

    # Return a ground-truth summary so the LLM doesn't have to guess at submitted values.
    h, rem = divmod(eff_duration, 3600)
    m, s = divmod(rem, 60)
    summary = [
        f"job_id: {job_id}",
        f"cluster: {cluster}",
        f"nodes: {eff_nodes}",
        f"duration: {h}:{m:02d}:{s:02d} ({eff_duration}s)",
    ]
    # The rendered paths, so a caller can record where this job's files are:
    # without them, the report attached to a debate's FINDING post named no
    # file a human could go and read.
    if not dry_run.enabled():
        summary += [
            f"log_path: {log_path}",
            f"err_path: {err_path}",
            f"output_dir: {output_dir}",
        ]
    return "\n".join(summary)


async def _submit_odo_job(
    cfg: UserConfig, job: str, node_count: int | None, duration_int: int | None, script_args: str | None,
) -> tuple[str, str, str, int, int]:
    """
    Odo dispatch (OLCF open enclave): IRI compute + Globus file ops, the same
    architecture as Frontier (`_submit_frontier_job`). Differences:

    - open-enclave IRI endpoint (`settings.odo_iri_url`) with a pinned compute
      resource id (`settings.odo_compute_resource_id`)
    - the Slurm account is the S3M token's own project (see `_olcf_project`)
    - the setup snippet `cd`s into RUN_DIR_Odo so job.odo.slurm scripts that
      reference sources relative to the working dir keep working

    Files go where `RemoteLayout` says, beside the researcher's Odo remote
    directory (checked by `_require_writable_out`): the job runs as the
    project's IRI automation user, and Globus only ever creates what that user
    merely reads.

    Returns (job_id, rendered_stdout_path, rendered_stderr_path, rendered_output_dir, effective_node_count, effective_duration_seconds).
    """
    job_info = AVAILABLE_JOBS[job]
    defaults = job_info.cluster_defaults.odo
    if defaults is None:
        raise ValueError(f"Job '{job}' has no \"odo\" section in cluster_defaults.json")

    local_job_dir = settings.local_hpc_jobs_dir / job
    job_script_path = local_job_dir / ODO_JOB_SCRIPT
    if not job_script_path.exists():
        raise ValueError(
            f"Job '{job}' has no Odo script at {job_script_path}. "
            f"Add a {ODO_JOB_SCRIPT} to enable Odo submission."
        )

    # Read first, so an unset remote folder fails before anything is contacted;
    # the sources folder is named once Globus says who the researcher is.
    layout = _layout(cfg, "odo")
    s3m_token, project = await _olcf_project(cfg, "odo")
    iri_client = await create_odo_iri_client(iri_token=s3m_token)
    globus = create_globus_client(
        tokens=cfg.require_globus_token("odo"), cluster="odo",
    )
    layout = layout.with_user(
        await globus.home_owner(collection_id=settings.odo_globus_collection_id)
    )
    src_dir = layout.src(job)

    await _require_writable_out(
        globus, collection_id=settings.odo_globus_collection_id, layout=layout, cluster="odo",
    )
    await _sync_job_sources(
        globus, job, src_dir, parents_below=layout.parent,
        remote_endpoint=settings.odo_globus_collection_id,
    )

    job_script_text = job_script_path.read_text(encoding="utf-8")
    setup_script_path = local_job_dir / ODO_SETUP_SCRIPT
    pre_launch = (
        f"bash -lc {shlex.quote(setup_script_path.read_text(encoding='utf-8'))}"
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
    setup_snippet = "\n".join([_shared_out_prefix(layout.out, project), textwrap.dedent(f"""
        export VISTA_OUT={shlex.quote(layout.out)}/"$SLURM_JOB_ID"
        mkdir -p -m 2775 "$VISTA_OUT"

        export HOME="${{HOME:-$VISTA_OUT}}"

        export https_proxy="http://proxy.ccs.ornl.gov:3128"
        export http_proxy="http://proxy.ccs.ornl.gov:3128"
        export no_proxy="localhost,127.0.0.1,0.0.0.0"

        module purge 2>/dev/null || true

        cd {shlex.quote(src_dir)}
    """).strip()])
    job_cmd_args = shlex.join(shlex.split(script_args or ""))
    body_lines = [setup_snippet]
    if job_cmd_args:
        body_lines.append(f"set -- {job_cmd_args}")
    body_lines.append(job_script_text)
    job_cmd = "\n".join(body_lines) + "\n"

    # Odo-suffixed env vars, mirroring RUN_DIR_Frontier / RUN_DIR_Perlmutter.
    iri_env = {
        "RUN_DIR_Odo": src_dir,
        "FORGE_MODEL_Odo": f"{layout.job_dir(job)}/model",
    }
    iri_env.update(defaults.iri.environment)  # user-supplied JSON entries win

    if defaults.iri.image is not None:
        iri_env["VISTA_ODO_IMAGE"] = defaults.iri.image
    if defaults.iri.module is not None:
        iri_env["VISTA_ODO_MODULE"] = defaults.iri.module


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
            "account": project,
            "duration": duration,
            **({"custom_attributes": {"constraint": defaults.iri.constraint}} if defaults.iri.constraint else {}),
            **({"pre_launch": pre_launch} if pre_launch else {}),
            "directory": layout.jobs,
            "stdout_path": layout.stdout_template,
            "stderr_path": layout.stderr_template,
            "environment": iri_env,
        },
    }
    job_id = await iri_client.submit_job(spec, name=f"vista-{job}")
    logging.info(f"Submitted job {job_id} via IRI to odo")
    return (
        job_id,
        layout.log_path(job_id),
        layout.err_path(job_id),
        layout.output_dir(job_id),
        nodes,
        duration,
    )


async def _require_writable_out(
    globus: GlobusClient, *, collection_id: str, layout: RemoteLayout, cluster: Cluster,
) -> None:
    """
    Refuse an Odo or Frontier submission when the job could not create its
    output folder, `<base>.out`.

    The job runs as the project's IRI automation user, and Slurm creates
    `<base>.out` for the job's logs as that user. That needs the folder holding
    `<base>` to be writable by the project's group; when it is not, the job
    dies at log creation with no log to say why, so this turns it into an error
    naming the command that fixes it. VISTA cannot create the folder itself: one
    made through Globus belongs to the researcher, 755, and Globus cannot chmod.

    Two Transfer `stat`s at most, each reading one entry. An existing `.out` is
    accepted whatever its mode: one made by Slurm is 755 but owned by the
    automation user, so its mode says nothing about whether that user may write
    in it, and every job keeps it group-writable anyway (`_shared_out_prefix`).
    When an entry cannot be read -- common above a project's own directories --
    the answer is unknown and the submission goes ahead, rather than refusing on
    a guess.
    """
    title = cluster.title()
    parent = layout.parent

    def refuse(reason: str) -> NoReturn:
        raise ToolError(
            f"Your {title} jobs could not create their output folder {layout.out}: "
            f"{reason} Create it once, on {title}:\n  mkdir -p -m 2775 {layout.out}\n"
            "Jobs run as your project's IRI automation user, so the folder must be "
            "writable by the project's group."
        )

    try:
        out = await globus.operation_stat(endpoint=collection_id, path=layout.out)
    except GlobusSessionExpired:
        # Says which connection to redo; the messages below would say the wrong thing.
        raise
    except GlobusFileNotFound:
        out = None
    except Exception as e:
        logging.warning(f"could not stat {layout.out} on {title} ({e}); submitting anyway")
        return
    if out is not None:
        if out.get("type") != "dir":
            refuse(f"{layout.out} is not a folder.")
        return

    try:
        entry = await globus.operation_stat(endpoint=collection_id, path=parent)
    except GlobusSessionExpired:
        raise
    except GlobusFileNotFound:
        refuse(f"{parent} does not exist.")
    except Exception as e:
        logging.warning(
            f"could not read the permissions of {parent} on {title} ({e}); submitting anyway"
        )
        return
    perms = entry.get("permissions")
    try:
        group_writable = int(perms, 8) & 0o020 if perms else True
    except ValueError:
        group_writable = True  # a format we cannot read; do not refuse on it
    if not group_writable:
        refuse(f"{parent} is not writable by its group (permissions {perms}).")


async def _submit_perlmutter_job(
    cfg: UserConfig, job: str, node_count: int | None, duration_int: int | None, script_args: str | None,
) -> tuple[str, str, str, int, int]:
    """ Returns (job_id, rendered_stdout_path, rendered_stderr_path, rendered_output_dir, effective_node_count, effective_duration_seconds). """
    if not cfg.nersc_account:
        raise ToolError(
            "No NERSC account configured for this user. Set it in the Vista user "
            "settings page before submitting jobs to Perlmutter."
        )
    layout = _layout(cfg, "perlmutter")

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
    src_dir = layout.src(job)
    await _sync_perlmutter_sources(iri_client, job, src_dir)
    # Perlmutter runs as the researcher, so VISTA can make the log folder
    # itself; NERSC's Slurm has not been confirmed to create a missing one, as
    # OLCF's does.
    await iri_client.mkdir(layout.out)

    job_script_text = job_script_path.read_text(encoding="utf-8")
    setup_script_path = local_job_dir / PERLMUTTER_SETUP_SCRIPT
    pre_launch = (
        f"bash -lc {shlex.quote(setup_script_path.read_text(encoding='utf-8'))}"
        if setup_script_path.exists() else None
    )

    nodes = node_count or defaults.resources.node_count or 1
    workers_per_node = defaults.resources.processes_per_node or 1
    duration = duration_int or defaults.duration

    # Mirror the Odo/Frontier setup-snippet UX: the user's job.perlmutter.slurm runs
    # with $VISTA_OUT set to a per-job-id output dir that's already mkdir'd.
    #
    # No `_shared_out_prefix`: the job runs as the researcher in their own folder,
    # which nothing else writes, so NERSC's default permissions stay as they are.
    setup_snippet = textwrap.dedent(f"""
        export VISTA_OUT={shlex.quote(layout.out)}/"$SLURM_JOB_ID"
        mkdir -p "$VISTA_OUT"
    """).strip()
    job_cmd_args = shlex.join(shlex.split(script_args or ""))
    body_lines = [setup_snippet]
    if job_cmd_args:
        body_lines.append(f"set -- {job_cmd_args}")
    body_lines.append(job_script_text)
    job_cmd = "\n".join(body_lines) + "\n"

    # Convention-driven layout inside the NERSC remote directory (see
    # `RemoteLayout`): sources in <remote_dir>/jobs/<job>/src, the downloaded
    # model in <remote_dir>/out/<job>/model. The user's job.perlmutter.slurm reads
    # RUN_DIR_Perlmutter and FORGE_MODEL_Perlmutter from the job environment.
    # Advanced users can override either by setting iri.environment in cluster_defaults.json.
    iri_env = {
        "RUN_DIR_Perlmutter": src_dir,
        "FORGE_MODEL_Perlmutter": f"{layout.job_dir(job)}/model",
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
            "directory": layout.jobs,
            "stdout_path": layout.stdout_template,
            "stderr_path": layout.stderr_template,
            "environment": iri_env,
        },
    }
    job_id = await iri_client.submit_job(spec, name=f"vista-{job}")
    logging.info(f"Submitted job {job_id} via IRI to {settings.nersc_machine}")
    return (
        job_id,
        layout.log_path(job_id),
        layout.err_path(job_id),
        layout.output_dir(job_id),
        nodes,
        duration,
    )


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
    the researcher's own Globus connection grants access to the OLCF DTN.

    The Slurm account is the S3M token's own project (see `_olcf_project`).

    Returns (job_id, rendered_stdout_path, rendered_stderr_path, rendered_output_dir, effective_node_count, effective_duration_seconds).
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

    # Read first, so an unset remote folder fails before anything is contacted;
    # the sources folder is named once Globus says who the researcher is.
    layout = _layout(cfg, "frontier")
    s3m_token, project = await _olcf_project(cfg, "frontier")
    iri_client = await create_olcf_iri_client(iri_token=s3m_token)
    globus = create_globus_client(
        tokens=cfg.require_globus_token("frontier"), cluster="frontier",
    )
    layout = layout.with_user(
        await globus.home_owner(collection_id=settings.frontier_globus_collection_id)
    )
    src_dir = layout.src(job)

    # File ops via Globus, which only ever creates the source tree the job
    # reads. Everything the job writes is created by Slurm and the job itself,
    # as the project's IRI automation user, beside it (see `RemoteLayout`).
    await _require_writable_out(
        globus, collection_id=settings.frontier_globus_collection_id, layout=layout,
        cluster="frontier",
    )
    await _sync_job_sources(
        globus, job, src_dir, parents_below=layout.parent,
        remote_endpoint=settings.frontier_globus_collection_id,
    )

    job_script_text = job_script_path.read_text(encoding="utf-8")
    setup_script_path = local_job_dir / FRONTIER_SETUP_SCRIPT
    pre_launch = (
        f"bash -lc {shlex.quote(setup_script_path.read_text(encoding='utf-8'))}"
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
    setup_snippet = "\n".join([_shared_out_prefix(layout.out, project), textwrap.dedent(f"""
        export VISTA_OUT={shlex.quote(layout.out)}/"$SLURM_JOB_ID"
        mkdir -p -m 2775 "$VISTA_OUT"

        export HOME="${{HOME:-$VISTA_OUT}}"

        export https_proxy="http://proxy.ccs.ornl.gov:3128"
        export http_proxy="http://proxy.ccs.ornl.gov:3128"
        export no_proxy="localhost,127.0.0.1,0.0.0.0"

        module purge 2>/dev/null || true
    """).strip()])
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
        "FORGE_MODEL_Frontier": f"{layout.job_dir(job)}/model",
        "VISTA_JOB_DIR": layout.job_dir(job),
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
            "account": project,
            "duration": duration,
            **({"custom_attributes": {"constraint": defaults.iri.constraint}} if defaults.iri.constraint else {}),
            **({"pre_launch": pre_launch} if pre_launch else {}),
            "directory": layout.jobs,
            "stdout_path": layout.stdout_template,
            "stderr_path": layout.stderr_template,
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
    return (
        job_id,
        layout.log_path(job_id),
        layout.err_path(job_id),
        layout.output_dir(job_id),
        nodes,
        duration,
    )


async def _lux_conn(ctx: Context, tool: str, **call_args):
    """
    The researcher's SSH connection to a Lux login node, through the hub. Cached
    per chat session by `get_ssh_conn_mcp_elicitation`, so only the first Lux
    call in a session asks for passcodes; `tool`/`call_args` label that prompt.
    """
    return await get_ssh_conn_mcp_elicitation(
        ctx,
        message=get_tool_call_string(tool, cluster="lux", **call_args),
        host=list(settings.lux_ssh_hosts),
    )


def _lux_exports(env: dict[str, str]) -> str:
    return "\n".join(f"export {k}={shlex.quote(str(v))}" for k, v in env.items())


def _lux_proxy_env() -> dict[str, str]:
    if not settings.lux_proxy:
        return {}
    return {
        "https_proxy": settings.lux_proxy,
        "http_proxy": settings.lux_proxy,
        "no_proxy": "localhost,127.0.0.1,0.0.0.0",
    }


async def _submit_lux_job(
    ctx: Context, cfg: UserConfig, job: str, node_count: int | None, duration_int: int | None, script_args: str | None,
) -> tuple[str, str, str, int, int]:
    """
    Lux dispatch: plain Slurm over SSH, since Lux has no IRI service.

    Same layout and job-script contract as Frontier (Lux mounts the same Orion
    Lustre), so a job's `job.lux.slurm` sees `VISTA_OUT`, `RUN_DIR_Lux`, the
    proxy, and its `cluster_defaults.json` environment. The differences:

    - Jobs run as the researcher (their SSH login), not a project service user,
      so there is no S3M introspection. The Slurm account is the researcher's
      Lux account setting, since there is no token to take a project from.
    - Sources go up over SFTP on the same connection instead of Globus.
    - `setup_lux.sh`, if present, runs on the LOGIN node before `sbatch`, where
      the network (via the proxy) is: it is the place to clone or update code.
      A failure there is a tool error at submit time, not a failed job later.
    - Resources become `#SBATCH` directives; the job body goes to `sbatch` on
      stdin, so nothing but outputs is written per submission.

    Returns (job_id, rendered_stdout_path, rendered_stderr_path, rendered_output_dir, effective_node_count, effective_duration_seconds).
    """
    job_info = AVAILABLE_JOBS[job]
    defaults = job_info.cluster_defaults.lux
    if defaults is None:
        raise ValueError(f"Job '{job}' has no \"lux\" section in cluster_defaults.json")

    local_job_dir = settings.local_hpc_jobs_dir / job
    job_script_path = local_job_dir / LUX_JOB_SCRIPT
    if not job_script_path.exists():
        raise ValueError(
            f"Job '{job}' has no Lux script at {job_script_path}. "
            f"Add a {LUX_JOB_SCRIPT} to enable Lux submission."
        )

    # Read first, so an unset remote folder fails before anyone is asked to log
    # in; the sources folder is named once the SSH login says who they are.
    layout = _layout(cfg, "lux")
    account = cfg.require_lux_account()
    nodes = node_count or defaults.resources.node_count or 1
    duration = duration_int or defaults.duration

    conn = await _lux_conn(
        ctx, "submit_hpc_job", job=job, node_count=nodes, duration=duration, script_args=script_args,
    )

    layout = layout.with_user(await slurm_ssh.username(conn))
    src_dir = layout.src(job)
    await _sync_job_sources_ssh(conn, job, src_dir)
    # Lux runs as the researcher, so VISTA can make the log folder itself, as on
    # Perlmutter. Odo's and Frontier's Slurm were seen creating a missing one;
    # Lux's has not been checked, and Slurm upstream does not. Made shared the
    # same way every job prefix does it (see `_shared_out_prefix`).
    await slurm_ssh.makedirs(conn, layout.out)
    await slurm_ssh.run(conn, _shared_out_prefix(layout.out, account))

    job_env = {
        "RUN_DIR_Lux": src_dir,
        "VISTA_JOB_DIR": layout.job_dir(job),
        **defaults.iri.environment,  # user-supplied JSON entries win
    }

    setup_script_path = local_job_dir / LUX_SETUP_SCRIPT
    if setup_script_path.exists():
        setup_cmd = "\n".join([
            "umask 002",  # what it creates in `.out`, a Frontier job may update too
            _lux_exports({**_lux_proxy_env(), **job_env}),
            setup_script_path.read_text(encoding="utf-8"),
        ])
        result = await slurm_ssh.run(conn, setup_cmd, login_shell=True)
        if result.exit_status != 0:
            tail = "\n".join(result.output.splitlines()[-40:])
            raise ToolError(f"{LUX_SETUP_SCRIPT} failed on the Lux login node (exit {result.exit_status}):\n{tail}")
        logging.info(f"{LUX_SETUP_SCRIPT} for {job} OK:\n{result.output[-2000:]}")

    body_lines = [
        _shared_out_prefix(layout.out, account),
        _lux_exports(job_env),
        f'export VISTA_OUT={shlex.quote(layout.out)}/"$SLURM_JOB_ID"',
        'mkdir -p -m 2775 "$VISTA_OUT"',
    ]
    if _lux_proxy_env():
        body_lines.append(_lux_exports(_lux_proxy_env()))
    job_cmd_args = shlex.join(shlex.split(script_args or ""))
    if job_cmd_args:
        body_lines.append(f"set -- {job_cmd_args}")
    body_lines.append(job_script_path.read_text(encoding="utf-8"))

    script = slurm_ssh.render_batch_script(
        job_name=f"vista-{job}",
        account=account,
        node_count=nodes,
        duration_s=duration,
        stdout_path=layout.stdout_template,
        stderr_path=layout.stderr_template,
        workdir=layout.jobs,
        body="\n".join(body_lines) + "\n",
        # queue_name has an IRI-side default ("regular", Perlmutter's QOS); on Lux
        # only a queue the job's JSON names explicitly is passed to Slurm.
        partition=defaults.iri.partition,
        queue=defaults.iri.queue_name if "queue_name" in defaults.iri.model_fields_set else None,
        constraint=defaults.iri.constraint,
        exclusive=bool(defaults.resources.exclusive_node_use),
        ntasks_per_node=defaults.resources.processes_per_node,
        gpus_per_node=defaults.resources.gpus_per_node,
    )
    try:
        job_id = await slurm_ssh.sbatch(conn, script)
    except slurm_ssh.SlurmSshError as e:
        raise ToolError(str(e)) from e
    logging.info(f"Submitted job {job_id} via sbatch to lux")
    return (
        job_id,
        layout.log_path(job_id),
        layout.err_path(job_id),
        layout.output_dir(job_id),
        nodes,
        duration,
    )


async def _sync_job_sources_ssh(conn, job: str, src_dir: str) -> None:
    """
    `_sync_job_sources` for an SSH cluster: the same upload set, and the same
    rule for skipping it (every file already there at its full length -- see
    the reasoning there), moved over SFTP instead of Globus.
    """
    local_job_dir = settings.local_hpc_jobs_dir / job
    sources = [
        f for f in sorted(local_job_dir.iterdir())
        if f.is_file() and not f.name.startswith(".")
        and f.name not in _HPC_JOB_METADATA_FILES
    ]
    if not sources:
        # Still made: `<base>.jobs` is the job's working directory.
        logging.warning(f"no source files to upload from {local_job_dir} (only metadata?)")
        await slurm_ssh.makedirs(conn, src_dir)
        return

    existing = await slurm_ssh.list_file_sizes(conn, src_dir)
    stale = [f for f in sources if existing.get(f.name) != f.stat().st_size]
    if not stale:
        logging.debug(f"src dir {src_dir} already holds every source; skipping upload")
        return
    await slurm_ssh.makedirs(conn, src_dir)
    await slurm_ssh.upload_files(conn, stale, src_dir)
    logging.info(f"Uploaded {len(stale)} source file(s) to {src_dir}: {[f.name for f in stale]}")


async def _sync_job_sources(
    globus: GlobusClient, job: str, src_dir: str, *, parents_below: str, remote_endpoint: str,
) -> None:
    """
    Upload `hpc_jobs/<job>/` (minus orchestration metadata) to `src_dir`, one
    HTTPS `PUT` per file. Shared by the Odo and Frontier dispatchers — they
    differ only in which OLCF collection `remote_endpoint` points at.
    Idempotent: files already on the collection are not re-sent, and a
    submission that follows a partly-failed one uploads only what is missing.

    `parents_below` is the deepest folder assumed to exist already -- the one
    holding the remote folder -- and the floor for the recursive mkdir.

    Permissions: the project's IRI automation user only needs to READ the src
    tree, which the DTN's default umask grants (755 dirs / 644 files).
    Globus-created dirs are NOT group-writable, which is why everything the
    job WRITES lives in `<base>.out` instead (see `RemoteLayout`).
    Pre-creating `<base>.jobs/<job>/src` manually also works — the mkdir here
    is idempotent and the transfer just adds files.
    """
    # Stage the upload set: scan local_hpc_jobs_dir/<job> for files to push.
    local_job_dir = settings.local_hpc_jobs_dir / job
    sources = [
        f for f in sorted(local_job_dir.iterdir())
        if f.is_file() and not f.name.startswith(".")
        and f.name not in _HPC_JOB_METADATA_FILES
    ]

    if not sources:
        # Still made: `<base>.jobs` is the job's working directory.
        logging.warning(f"no source files to upload from {local_job_dir} (only metadata?)")
        await globus.operation_mkdir_p(
            endpoint=remote_endpoint, path=src_dir, parents_below=parents_below,
        )
        return

    # Probe the remote collection, and skip only when EVERY source is already
    # there AT ITS FULL LENGTH. "Any entries at all" was enough when one Globus
    # transfer task moved the whole set atomically. One PUT per file is not
    # atomic: a failure part way leaves src_dir non-empty, and a probe that
    # asked only whether it was empty would skip the upload on the next
    # submission and run the job against half a source tree — which fails on the
    # cluster, saying nothing about why. A file left TRUNCATED by an interrupted
    # PUT keeps its name, so name alone is the same mistake one level down, and
    # the size Transfer reports is the only thing that tells them apart (the
    # HTTPS interface has no checksum, so this is also as far as verification
    # goes). An entry with no size at all — which Transfer does not do for a
    # file — counts as stale, since re-sending is the harmless direction.
    try:
        existing = {
            e.get("name"): e.get("size")
            for e in await globus.operation_ls(endpoint=remote_endpoint, path=src_dir)
            if e.get("type") == "file"
        }
        stale = [f for f in sources if existing.get(f.name) != f.stat().st_size]
        if not stale:
            logging.debug(f"src dir {src_dir} already holds every source; skipping upload")
            return
        if existing:
            logging.info(
                f"src dir {src_dir} is missing or truncated for {len(stale)} of "
                f"{len(sources)} source file(s); uploading {[f.name for f in stale]}"
            )
        sources = stale
    except GlobusSessionExpired:
        # Not "the directory is not there yet". Letting this fall through to the
        # upload below would spend the whole submission discovering the same
        # thing one PUT at a time, and report it as a failed upload.
        raise
    except Exception as e:
        # src_dir doesn't exist yet (or is unreachable for some other reason);
        # fall through to mkdir + upload.
        logging.debug(f"src dir {src_dir} not yet readable ({e}); creating + uploading")

    # mkdir -p `<base>.jobs/<job>/src` — Globus makes one level per call,
    # and a PUT into a missing parent is a 404, so this has to come first.
    await globus.operation_mkdir_p(
        endpoint=remote_endpoint, path=src_dir, parents_below=parents_below,
    )

    for f in sources:
        await globus.upload_file(
            collection_id=remote_endpoint,
            local_path=f,
            remote_path=f"{src_dir}/{f.name}",
        )
    logging.info(f"Uploaded {len(sources)} source file(s) to {src_dir}: {[f.name for f in sources]}")


async def _tail_remote_log(
    globus: GlobusClient, *, collection_id: str, remote_path: str, local_path: Path,
) -> str:
    """Fetch what is new in a growing remote log, and return its tail.

    Two requests: `HEAD` for the current size, then one `GET` for the bytes
    between what is already held locally and that size. The local file both
    accumulates the log and *is* the offset, so nothing has to be remembered
    between calls and a restart picks up where it left off.

    Safe against a file still being written. A range computed from a size that
    went stale between the two requests is still inside the file, and whatever
    was appended in between arrives on the next poll. `HEAD` is the only source
    of the size: a plain `GET` on these collections returns no `Content-Length`
    and a `206` reports its total as `*`.

    A remote file SHORTER than the local copy means it is not the same file --
    a rerun writing to the same path, or a truncation. Starting over is the
    only reading of that which cannot splice two different logs together.
    """
    local_path.parent.mkdir(parents=True, exist_ok=True)
    have = local_path.stat().st_size if local_path.exists() else 0
    size = await globus.stat(collection_id=collection_id, remote_path=remote_path)

    if size < have:
        logging.info(f"remote log {remote_path} shrank ({have} -> {size}); refetching")
        local_path.unlink(missing_ok=True)
        have = 0

    if size > have:
        new = await globus.read_range(
            collection_id=collection_id,
            remote_path=remote_path,
            start=have,
            end=size - 1,
        )
        with local_path.open("ab" if have else "wb") as handle:
            handle.write(new)
        logging.debug(f"appended {len(new)} byte(s) of {remote_path} at offset {have}")

    return _read_log_tail(local_path)


def _read_log_tail(path: Path) -> str:
    """The last `_LOG_TAIL_LINES` lines of a local log file.

    Reads only the final `_LOG_TAIL_BYTES` off disk -- a job can write far more
    than anyone wants to see, and the accumulated file keeps all of it.
    """
    if not path.exists():
        return ""
    with path.open("rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        handle.seek(max(0, size - _LOG_TAIL_BYTES))
        raw = handle.read()
    text = raw.decode("utf-8", errors="replace")
    if size > _LOG_TAIL_BYTES and "\n" in text:
        # The window almost certainly opened mid-line; a truncated first line
        # reads as corrupt output rather than as a window. Guarded on there
        # being a newline at all: one line longer than the window is better
        # shown truncated than dropped entirely.
        _, _, text = text.partition("\n")
    lines = text.splitlines()
    return "\n".join(lines[-_LOG_TAIL_LINES:])


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


async def _olcf_project(cfg: UserConfig, cluster: Cluster) -> tuple[str, str]:
    """
    The researcher's S3M token for an OLCF cluster, and the project it belongs to.

    The project is the job's Slurm account: IRI runs the job as that project's
    automation user, so it is the only account the token can charge. Any
    project is accepted -- the facility decides what the token may do, and file
    operations act as the researcher's own Globus identity.

    Only submission needs this. Status, outputs and cancel go straight to IRI
    and Globus, which authorize the researcher themselves.
    """
    url = settings.odo_introspect_url if cluster == "odo" else settings.frontier_introspect_url
    token = cfg.require_s3m_token(cluster)
    return token, await get_s3m_token_project(token, introspect_url=url)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
async def get_hpc_job_status(ctx: Context, job_id: str, cluster: Cluster) -> str:
    """
    Get the status and logs of a submitted HPC job.

    Args:
        job_id: The job id returned by submit_hpc_job
        cluster: Which cluster the job was submitted to, as submit_hpc_job reported it.
            Required: a job id is only unique within its cluster, and nothing else
            remembers which cluster a job went to.
    """
    job_id = validate_job_id(job_id)
    meta = get_vista_meta(ctx)
    cfg = meta.user

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
        if cluster == "lux":
            host_output_dir = Path(meta.project_paths.require_output_dir())
            return await _get_lux_job_status(ctx, cfg, host_output_dir, job_id)
        # Odo/Frontier accumulate the job's log on disk and tail it from there;
        # need the per-agent output dir from project_paths to know where it lands.
        host_output_dir = Path(meta.project_paths.require_output_dir())
        return await _get_olcf_job_status(cfg, host_output_dir, job_id, cluster=cluster)


async def _get_perlmutter_job_status(cfg: UserConfig, job_id: str) -> str:
    layout = _layout(cfg, "perlmutter")
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

    try:
        logs = await iri_client.head(layout.log_path(job_id), lines=200)
    except Exception as e:
        logs = f"(unable to fetch logs: {e})"

    # stderr as well as stdout. A job that failed wrote its reason here, and
    # reading only stdout is how a traceback became "no output".
    try:
        errs = await iri_client.head(layout.err_path(job_id), lines=200)
    except Exception as e:
        errs = f"(unable to fetch stderr: {e})"

    files: list[str] = []
    output_dir = layout.output_dir(job_id)
    try:
        ls_result = await iri_client.ls(output_dir, recursive=True)
        files = _flatten_ls_paths(ls_result, root=output_dir)[:20]
    except Exception as e:
        logging.info(f"output dir not readable yet ({output_dir}): {e}")

    return "\n\n".join([
        "\n".join(f"{k}={v}" for k, v in metadata.items()),
        "--- LOGS ---",
        logs.strip() if logs.strip() else "(no logs yet)",
        "--- STDERR ---",
        errs.strip() if errs.strip() else "(nothing on stderr)",
        "--- OUTPUT FILES ---",
        "\n".join(files) if files else "(no output files yet)",
    ])


async def _get_olcf_job_status(
    cfg: UserConfig, host_output_dir: Path, job_id: str, *, cluster: Cluster,
) -> str:
    """
    Shared Odo/Frontier status: IRI for state, Globus for the log tail + output
    file listing. The two clusters differ only in IRI endpoint and Globus
    collection (see `_create_olcf_iri_for` / `_olcf_collection_id`).

    The log is tailed incrementally: a `HEAD` for the current size, then one
    ranged `GET` for whatever is new since the last poll. Each query therefore
    costs the log output since the previous one rather than the whole log, and
    what comes back is the END of it — which is what a researcher watching a
    running or failed job is looking for.
    """
    layout = _layout(cfg, cluster)
    log_path, err_path, output_dir = (
        layout.log_path(job_id), layout.err_path(job_id), layout.output_dir(job_id),
    )
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

    # While the job is still waiting for resources, neither the log file nor the
    # output dir exists. Answer from the IRI state alone — and, since this comes
    # before the Globus client is built, without needing a Globus credential at
    # all to watch a queued job.
    if state in _PRE_RUN_STATES:
        return "\n\n".join([
            "\n".join(f"{k}={v}" for k, v in metadata.items()),
            "(job has not started yet; logs and outputs will appear once it runs)",
        ])

    # A status query is the one tool that must keep answering when Globus is
    # not connected. Whether the job succeeded is knowable from IRI alone, and
    # refusing the whole call would hide it behind a credential problem — which
    # is the same confusion, pointing the other way. So an absent or incomplete
    # credential degrades the logs and the file listing and nothing else.
    # An *expired* one still raises: that names a connection to redo, and is
    # raised from inside the client rather than here.
    #
    # BOTH halves have to say so. "(no output files yet)" under a job that
    # finished is the original bug's sentence, and a credential VISTA never had
    # renders it just as well as one that lapsed. The listing carries its own
    # reason for the same reason the log tail does.
    globus: GlobusClient | None = None
    logs = "(no logs yet)"
    listing = "(no output files yet)"
    try:
        globus = create_globus_client(
            tokens=cfg.require_globus_token(cluster), cluster=cluster,
        )
    except ToolError as e:
        logs = f"(logs unavailable: {e})"
        listing = f"(output files unavailable: {e})"

    # Tail the log: HEAD for the size, one ranged GET for what is new. The
    # local copy is the offset — its size is how much of the remote file we
    # already hold — so the state survives a server restart and needs no
    # registry of its own.
    local_log_path = host_output_dir / job_id / Path(log_path).name
    if globus is not None:
        try:
            logs = await _tail_remote_log(
                globus,
                collection_id=remote_collection,
                remote_path=log_path,
                local_path=local_log_path,
            )
        except GlobusFileNotFound:
            # The job has started but Slurm has not opened the log yet. Genuinely
            # "no logs yet", and distinguishable from an expired session by status
            # code alone — which is the point of the HTTPS interface here.
            pass
        except GlobusSessionExpired:
            # Says which connection to redo. Reported as a log-fetch failure it
            # would read as a broken job.
            raise
        except Exception as e:
            logs = f"(unable to fetch logs: {e})"

    # stderr, tailed the same way. Every cluster spec has always written one —
    # `stdout_path` and `stderr_path` are set side by side — but only stdout was
    # ever fetched, so a job that died with a traceback or an argparse usage
    # message looked silent: the `.out` file held the setup script's echoes and
    # stopped, and the reason sat in a file nothing here knew existed. Two debate
    # simulations reached an agent as "no outputs recorded" that way.
    #
    # `GlobusFileNotFound` is the good case, not a failure: Slurm creates the
    # stderr file only when something writes to it, so its absence *is* the
    # answer — and the HTTPS interface can tell that apart from a fetch that
    # could not be made, which is the difference between silence we verified and
    # silence we could not look at.
    if globus is None:
        errs = "(stderr unavailable: no Globus connection)"
    else:
        errs = "(nothing on stderr)"
        try:
            errs = (
                await _tail_remote_log(
                    globus,
                    collection_id=remote_collection,
                    remote_path=err_path,
                    local_path=host_output_dir / job_id / Path(err_path).name,
                )
                or "(nothing on stderr)"
            )
        except GlobusFileNotFound:
            pass
        except GlobusSessionExpired:
            raise
        except Exception as e:
            errs = f"(unable to fetch stderr: {e})"

    # List the output dir on the cluster's collection via Globus operation_ls
    # (recursive BFS-walk; see lib/globus.py). The HTTPS interface has no
    # listing, so this stays on Transfer. Drop venv/pycache noise.
    files: list[str] = []
    if globus is not None:
        # Pruned at the TRAVERSAL level, not just filtered out of the results below:
        # the walk costs one sequential API call per directory, so descending into a
        # venv/.git only to drop the entries afterwards is what turns a status check
        # into minutes of apparent hang.
        excludes = (".venv", "__pycache__", ".git", "node_modules")
        try:
            entries = await globus.operation_ls(
                endpoint=remote_collection,
                path=output_dir,
                recursive=True,
                exclude_segments=excludes,
            )
            for e in entries:
                if e.get("type") != "file":
                    continue
                p = e.get("path", "")
                rel = p[len(output_dir):].lstrip("/")
                # Whole path segments of the part under output_dir, never substrings
                # of the absolute path: `.gitignore`, `run.github.log`, or an
                # output_dir like `/proj/my.git-runs/` must not vanish from the listing.
                if any(seg in excludes for seg in rel.split("/")):
                    continue
                files.append(rel)
            files = files[:20]
        except GlobusSessionExpired:
            raise
        except Exception as e:
            # NOT "not readable yet": a recursive walk answers a directory that
            # does not exist with an empty list (the 404 is swallowed per
            # subtree in `lib/globus.py`), so reaching here is a real failure —
            # a 403, a collection that is down, a transport error — and it is
            # reported rather than dressed up as an empty directory.
            logging.warning(f"could not list output dir {output_dir}: {e}")
            listing = f"(output files unavailable: {e})"

    return "\n\n".join([
        "\n".join(f"{k}={v}" for k, v in metadata.items()),
        "--- LOGS ---",
        logs.strip() if logs.strip() else "(no logs yet)",
        "--- STDERR ---",
        errs.strip() if errs.strip() else "(nothing on stderr)",
        "--- OUTPUT FILES ---",
        "\n".join(files) if files else listing,
    ])


async def _get_lux_job_status(
    ctx: Context, cfg: UserConfig, host_output_dir: Path, job_id: str,
) -> str:
    """
    Lux status over the cached SSH connection: `squeue`/`sacct` for the state
    (reported in the same vocabulary as the IRI clusters, plus the raw Slurm
    state), the log tailed incrementally over SFTP by the same
    `_tail_remote_log` Odo/Frontier use, and `find` for the output listing.
    """
    layout = _layout(cfg, "lux")
    log_path, err_path, output_dir = (
        layout.log_path(job_id), layout.err_path(job_id), layout.output_dir(job_id),
    )
    conn = await _lux_conn(ctx, "get_hpc_job_status", job_id=job_id)
    js = await slurm_ssh.job_state(conn, job_id)

    metadata = {"JOB_ID": job_id, "CLUSTER": "lux", "STATE": js.state, "SLURM_STATE": js.slurm_state}
    if js.exit_code is not None:
        metadata["EXIT_CODE"] = js.exit_code
    if js.reason:
        metadata["REASON"] = js.reason
    header = "\n".join(f"{k}={v}" for k, v in metadata.items())

    if js.state in _PRE_RUN_STATES:
        return "\n\n".join([
            header,
            "(job has not started yet; logs and outputs will appear once it runs)",
        ])

    logs = "(no logs yet)"
    try:
        logs = await _tail_remote_log(
            slurm_ssh.SftpLogReader(conn),
            collection_id="lux",
            remote_path=log_path,
            local_path=host_output_dir / job_id / Path(log_path).name,
        )
    except slurm_ssh.RemoteFileNotFound:
        pass  # started, but Slurm has not opened the log yet
    except Exception as e:
        logs = f"(unable to fetch logs: {e})"

    # stderr, tailed the same way (see `_get_olcf_job_status` for why it is
    # fetched at all). A missing file is the good case: Slurm creates it only
    # when something writes to it.
    errs = "(nothing on stderr)"
    try:
        errs = (
            await _tail_remote_log(
                slurm_ssh.SftpLogReader(conn),
                collection_id="lux",
                remote_path=err_path,
                local_path=host_output_dir / job_id / Path(err_path).name,
            )
            or "(nothing on stderr)"
        )
    except slurm_ssh.RemoteFileNotFound:
        pass
    except Exception as e:
        errs = f"(unable to fetch stderr: {e})"

    listing = "(no output files yet)"
    files: list[str] = []
    try:
        files = await slurm_ssh.list_files(conn, output_dir)
    except Exception as e:
        logging.warning(f"could not list output dir {output_dir}: {e}")
        listing = f"(output files unavailable: {e})"

    return "\n\n".join([
        header,
        "--- LOGS ---",
        logs.strip() if logs.strip() else "(no logs yet)",
        "--- STDERR ---",
        errs.strip() if errs.strip() else "(nothing on stderr)",
        "--- OUTPUT FILES ---",
        "\n".join(files) if files else listing,
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
    ctx: Context, job_id: str, files: list[str], cluster: Cluster,
) -> str:
    """
    Download output files from an HPC job.

    Args:
        job_id: The job id returned by submit_hpc_job
        files: List of file paths to download. Relative to the jobs output directory (as shown by get_hpc_job_status).
        cluster: Which cluster the job was submitted to, as submit_hpc_job reported it.
            Required: a job id is only unique within its cluster, and nothing else
            remembers which cluster a job went to.

    Returns:
        The downloaded file paths.
    """
    job_id = validate_job_id(job_id)
    meta = get_vista_meta(ctx)
    cfg = meta.user
    host_output_dir = Path(meta.project_paths.require_output_dir())

    if cluster == "perlmutter":
        return await _get_perlmutter_job_outputs(cfg, host_output_dir, job_id, files)
    if cluster == "lux":
        return await _get_lux_job_outputs(ctx, cfg, host_output_dir, job_id, files)
    return await _get_olcf_job_outputs(cfg, host_output_dir, job_id, files, cluster=cluster)


SANDBOX_OUTPUT_DIR = PurePosixPath("/mnt/data/output")
"""Where job outputs appear inside the sandbox. A sandbox path, so POSIX on every host."""


def _job_output_paths(
    local_out_dir: Path, sandbox_out_dir: PurePosixPath, file: str
) -> tuple[Path, PurePosixPath]:
    """
    Map a requested output file name to its local download path and its sandbox path.

    The name must be relative and stay inside the job's output directory. It is checked as
    both a POSIX and a Windows path, because either reading could escape: on Windows
    `/etc/x` is not absolute, and on POSIX `..\\x` is not a parent reference. A backslash is
    refused outright: a Windows host would save `a\\b` as `a/b`, while the reported sandbox
    path would still say `a\\b`, a file that doesn't exist.
    """
    posix, windows = PurePosixPath(file), PureWindowsPath(file)
    if (
        not file
        or "\\" in file
        or posix.is_absolute()
        or windows.anchor
        or ".." in posix.parts
        or ".." in windows.parts
    ):
        raise ValueError(f'Invalid path "{file}"')
    local_path = local_out_dir.joinpath(*posix.parts)
    if not local_path.resolve().is_relative_to(local_out_dir.resolve()):
        raise ValueError(f'Invalid path "{file}"')
    return local_path, sandbox_out_dir.joinpath(*posix.parts)


async def _get_perlmutter_job_outputs(cfg: UserConfig, host_output_dir: Path, job_id: str, files: list[str]) -> str:
    # IRI filesystem download is currently text-only; binary checkpoints are not supported here.
    remote_out_dir = _layout(cfg, "perlmutter").output_dir(job_id)

    iri_client = await create_iri_client(iri_token=cfg.require_nersc_iri_token())
    local_out_dir = host_output_dir / job_id
    sandbox_out_dir = SANDBOX_OUTPUT_DIR / job_id

    downloaded = []
    for file in files:
        local_path, sandbox_path = _job_output_paths(local_out_dir, sandbox_out_dir, file)
        remote_path = f"{remote_out_dir}/{file}"
        local_path.parent.mkdir(parents=True, exist_ok=True)
        await iri_client.download(remote_path, local_path)
        downloaded.append(str(sandbox_path))

    return "Downloaded files:\n" + "\n".join(downloaded)


async def _get_olcf_job_outputs(
    cfg: UserConfig, host_output_dir: Path, job_id: str, files: list[str], *, cluster: Cluster,
) -> str:
    """
    Shared Odo/Frontier output retrieval: one streaming HTTPS `GET` per file off
    the cluster's own collection. Binary files (.pt checkpoints etc.) work
    natively — this is a byte stream, not a text API.

    No size cap, deliberately. Bulk data is meant to stay on the cluster and be
    processed by another job there; this tool exists for the small artifacts an
    agent works on locally, and a cap would be a guess at which is which.

    Files already present locally under host_output_dir/<job_id>/ are NOT
    re-fetched, so a follow-up `display_file` costs nothing. To force a fresh
    pull (e.g. a checkpoint updated mid-training), delete the local copy first.
    """
    remote_out_dir = _layout(cfg, cluster).output_dir(job_id)
    local_out_dir = host_output_dir / job_id
    sandbox_out_dir = SANDBOX_OUTPUT_DIR / job_id

    wanted: list[tuple[str, Path]] = []
    sandbox_paths: list[str] = []
    cached_paths: list[str] = []
    for file in files:
        # The HTTPS interface acts as the researcher's own mapped POSIX identity,
        # so the facility enforces what they may read. This relative-path check
        # plus the server-defined remote_out_dir confine the fetch to the
        # resolved output dir on top of that.
        local_path, sandbox_path = _job_output_paths(local_out_dir, sandbox_out_dir, file)
        sandbox_paths.append(str(sandbox_path))
        if local_path.exists() and local_path.stat().st_size > 0:
            cached_paths.append(str(sandbox_path))
            continue
        wanted.append((f"{remote_out_dir}/{file}", local_path))

    if wanted:
        globus = create_globus_client(
            tokens=cfg.require_globus_token(cluster), cluster=cluster,
        )
        for remote_path, local_path in wanted:
            await globus.download_file(
                collection_id=_olcf_collection_id(cluster),
                remote_path=remote_path,
                local_path=local_path,
            )
        logging.info(f"Globus-fetched {len(wanted)} file(s); served {len(cached_paths)} from local cache")
    else:
        logging.info(f"All {len(cached_paths)} requested files served from local cache (no Globus call)")

    return "Downloaded files:\n" + "\n".join(sandbox_paths)


async def _get_lux_job_outputs(
    ctx: Context, cfg: UserConfig, host_output_dir: Path, job_id: str, files: list[str],
) -> str:
    """
    Lux output retrieval over SFTP on the cached SSH connection. Same contract
    as `_get_olcf_job_outputs`: binary-safe, no size cap, and files already held
    locally are not fetched again.
    """
    remote_out_dir = _layout(cfg, "lux").output_dir(job_id)
    local_out_dir = host_output_dir / job_id
    sandbox_out_dir = SANDBOX_OUTPUT_DIR / job_id

    wanted: list[tuple[str, Path]] = []
    sandbox_paths: list[str] = []
    for file in files:
        local_path, sandbox_path = _job_output_paths(local_out_dir, sandbox_out_dir, file)
        sandbox_paths.append(str(sandbox_path))
        if not (local_path.exists() and local_path.stat().st_size > 0):
            wanted.append((f"{remote_out_dir}/{file}", local_path))

    if wanted:
        conn = await _lux_conn(ctx, "get_hpc_job_outputs", job_id=job_id, files=files)
        for remote_path, local_path in wanted:
            await slurm_ssh.download_file(conn, remote_path, local_path)
    logging.info(f"SFTP-fetched {len(wanted)} file(s); served {len(files) - len(wanted)} from local cache")
    return "Downloaded files:\n" + "\n".join(sandbox_paths)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True))
async def cancel_hpc_job(ctx: Context, job_id: str, cluster: Cluster) -> str:
    """
    Cancel a queued or running HPC job.

    Args:
        job_id: The job id returned by submit_hpc_job.
        cluster: Which cluster the job was submitted to, as submit_hpc_job reported it.
            Required: a job id is only unique within its cluster, and nothing else
            remembers which cluster a job went to.

    Returns:
        Confirmation of the cancellation request.
    """
    job_id = validate_job_id(job_id)
    if dry_run.is_dry_job(job_id):
        return dry_run.cancel(job_id)
    cfg = get_vista_meta(ctx).user
    if cluster == "lux":
        conn = await _lux_conn(ctx, "cancel_hpc_job", job_id=job_id)
        try:
            await slurm_ssh.scancel(conn, job_id)
        except slurm_ssh.SlurmSshError as e:
            raise ToolError(str(e)) from e
        logging.info(f"Cancelled job {job_id} on lux")
        return f"Cancellation requested for job {job_id} on lux."
    if cluster == "perlmutter":
        iri_client = await create_iri_client(iri_token=cfg.require_nersc_iri_token())
    else:  # "odo" / "frontier"
        iri_client = await _create_olcf_iri_for(cluster, cfg)
    await iri_client.cancel_job(job_id)
    logging.info(f"Cancelled job {job_id} on {cluster}")
    return f"Cancellation requested for job {job_id} on {cluster}."
