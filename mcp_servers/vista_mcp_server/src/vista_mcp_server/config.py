import os, sys, logging
from fastmcp.exceptions import ToolError
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import BaseModel, Field
from pathlib import Path
from typing import Annotated as A, Literal
import getpass
import uuid
from datetime import datetime
from .lib.types import ResolvedPath, CommaSeparatedList
from .metrics import MetricsSettings


class FaultSettings(BaseModel):
    """
    Fault-injection knobs (evaluation plan M7), read from env as
    `VISTA_MCP_FAULT__<FIELD>`. Behavior-changing experiment flags (§2a
    Group 3): the startup guard refuses any non-default value when
    `VISTA_ENV=prod`. All default to inert. Power E7a (recovery rate by
    fault type).
    """

    submit_fail_p: float = Field(default=0.0, ge=0.0, le=1.0)
    """Probability an HPC submission fails with a synthetic error."""

    status_timeout_p: float = Field(default=0.0, ge=0.0, le=1.0)
    """Probability a status poll fails with a synthetic timeout."""

    token_expire_after_s: float = 0.0
    """When > 0, HPC tool calls fail with a simulated token-expiry error
    once this many seconds have elapsed since the first HPC call in the
    process — exercising the cross-token-expiry recovery path honestly
    (paper §VI; E7b's 24 h case). 0 disables."""

    def is_active(self) -> bool:
        return bool(self.submit_fail_p or self.status_timeout_p or self.token_expire_after_s)

class S3Settings(BaseModel):
    """
    AWS S3 object store used to get job output off clusters whose IRI service
    has no storage scope (Odo, Frontier). Read from env as
    `VISTA_MCP_S3__<FIELD>`.
    """

    bucket: str | None = None
    """ Bucket that job output is pushed to and read back from. """

    region: str = "us-east-2"
    """ Bucket region, used for SigV4 signing on the cluster side too. """

    key_id: str | None = None
    """
    Access key id used for the job's output push and for Vista's own reads.
    Unset means Vista reads via the boto3 default chain and submissions fail.
    """

    secret: str | None = None
    """ Secret for `key_id`. Visible on the cluster — see the class docstring. """

    endpoint: str | None = None
    """
    Endpoint override for a non-AWS S3-compatible store. The escape hatch if the
    OLCF proxy turns out not to reach `*.s3.amazonaws.com`: point this at an
    ORNL-side object store and neither the uploader nor `lib/s3.py` changes.
    """


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VISTA_MCP_",
        env_file=[p / '.env' for p in reversed([Path.cwd(), *Path.cwd().parents])],
        extra='ignore',
        # Double underscore separates a nested sub-model's field name in env
        # vars, so the MetricsSettings sub-model below is overridable as
        # `VISTA_MCP_METRICS__LEVEL=...`. Flat top-level fields (no `__` in
        # their env-var name) are unaffected.
        env_nested_delimiter="__",
    )

    env: A[Literal['dev', 'prod'], Field(validation_alias="VISTA_ENV")] = 'dev'
    """
    Deployment marker, shared with the backend (`VISTA_ENV`). When `prod`,
    the startup guard (`assert_experiment_flags_allowed`) refuses the §2a
    Group 3 experiment flags (dry-run, queue delay, faults) so they can
    never be left on in production.
    """

    allowed_uris: list[str] = ["file://.*"]
    """
    List of regex patterns. A URI must match at least one to be allowed.
    This is used to limit what files the display_file tool can render.
    """

    # uri_map: dict[str, str] = {
    #     "file:///mnt/skills/": f"file://{Path('../../skills').resolve()}/",
    #     "file:///mnt/data/output/": f"file://{Path('../../data/output').resolve()}/",
    #     "file:///mnt/data/uploads/": f"file://{Path('../../data/uploads').resolve()}/",
    # }
    # """
    # Mapping of URI prefixes to replacement prefixes, applied after allowed_uris checks.
    # Allows mapping paths inside the sandboxed container to paths outside for the display_file tool.
    # """

    disable_servers: CommaSeparatedList[str] = ["agenthpc"]
    """
    Names of MCP subservers to skip mounting in server.py. Valid entries:
    "submit_job", "display_file", "rag", "agenthpc", "omd".
    agenthpc is disabled by default — its SSH requirements don't work in the AWS deployment.
    """

    mcp_apps_dir: ResolvedPath = Path(__file__).parent / 'mcp-apps'

    jobscripts_dir: ResolvedPath = Path(__file__).parent / "jobscripts"
    """
    Helper scripts that run *on the cluster*, inlined into the IRI JobSpec by
    the dispatchers (currently `s3_put.py`, the output push). Deliberately not
    under `local_hpc_jobs_dir`: `get_available_jobs()` raises at import for any
    directory there without a cluster job script.
    """

    dockerfile: ResolvedPath = Path(__file__).parent / "docker/Dockerfile"
    image: str = "vista-sandbox"

    local_hpc_jobs_dir: ResolvedPath = Path("../../hpc_jobs")

    odo_iri_url: str = "https://amsc-open.s3m.olcf.ornl.gov"
    """ Base URL for the OLCF AmSC IRI API on the open enclave """
    odo_account: str = "gen150-vista"
    """
    OLCF project name used as the Slurm account for Odo jobs. The user's S3M token must belong to
    this project.
    """
    odo_remote_dir: str = "/gpfs/wolf2/olcf/gen150/proj-shared/vista"
    """ Base dir on Odo where job sources and outputs live """
    odo_machine: str = "odo"
    """ OLCF compute resource group name (used to match the IRI discovery result). """
    odo_compute_resource_id: str = "70e0dde0-88e4-52e3-89f3-4849760f2e87"
    """
    Pinned IRI compute resource UUID for Odo. Bypasses `discover()` since the
    open-enclave service lists Odo / Defiant / Wombat / Quokka without a stable
    name/group match for `odo_machine`. Look up via amscrot's `discover()` if
    OLCF rotates resource ids.
    """
    odo_introspect_url: str = "https://s3m.olcf.ornl.gov/olcf/v1/token/ctls/introspect"
    """
    S3M token introspection endpoint used to verify that a user's token belongs
    to `odo_account` before Vista serves them that project's job output
    """
    frontier_iri_url: str = "https://amsc-moderate.s3m.olcf.ornl.gov"
    """ Base URL for the OLCF AmSC IRI API on the moderate enclave """
    frontier_account: str = "chm243"
    """
    OLCF project name used as the Slurm account for Frontier jobs. The user's S3M token must
    belong to this project.
    """
    frontier_remote_dir: str = "/lustre/orion/chm243/proj-shared/vista"
    """ Base dir on Frontier where job sources and outputs live """
    frontier_introspect_url: str = "https://s3m.olcf.ornl.gov/olcf/v1/token/ctls/introspect"
    """ Same as `odo_introspect_url`, for Frontier tokens (`frontier_account`). """
    frontier_machine: str = "frontier"
    """ OLCF compute resource group name (used to match the IRI discovery result). """
    nersc_iri_url: str = "https://api.iri.nersc.gov"
    """ Base URL for the NERSC IRI API. """
    nersc_machine: str = "perlmutter"
    """ NERSC compute resource group name (used to match the IRI discovery result). """

    globus_native_app_client_id: str = "fae5c579-490a-4d76-b6eb-d78f65caeb63"
    """
    Globus Native App client UUID used by `./scripts/get_globus_token.py
    --cluster perlmutter` to mint the per-user NERSC IRI token. NERSC's IRI
    tokens are issued by Globus Auth; this is unrelated to file transfer, which
    on Perlmutter rides the IRI filesystem API and on OLCF is an S3 push.
    """

    hpc_ssh_host: CommaSeparatedList[str] = ["login1.odo.olcf.ornl.gov"]
    """
    Legacy SSH host list, kept for the optional agenthpc subserver (disabled by
    default). The Odo/Frontier job tools no longer SSH — compute goes through
    IRI and job output comes back via S3. To use a jump host, pass an array or
    comma separated list of hosts.
    """
    hpc_ssh_user: str | None = None
    """ Legacy SSH user for the agenthpc subserver. No longer required at boot. """

    session_id: A[str, Field(default_factory=lambda: f"{getpass.getuser()}-{datetime.now().strftime("%Y%m%dT%H%M%S")}-{uuid.uuid4().hex[:8]}")]
    """ Unique id for the Vista session """

    omd_url: str = "https://api.i2-core.american-science-cloud.org/mcp/openmetadata"
    omd_api_key: str | None = None
    """
    Key for the AmSC Open Metadata Server.
    Same key as the AmSC inference API, get it from https://api.i2-core.american-science-cloud.org
    """

    data_dir: A[ResolvedPath, Field(validation_alias="VISTA_DATA_DIR")] = Path("../../data")
    """ Directory for data such as sandbox volumes and other created files """

    @property
    def knowledge_bases_dir(self) -> Path:
        """
        Root directory the MCP server scans at startup to discover the
        Knowledge Bases available to `rag_search`. Each immediate
        subdirectory whose name is a valid KB slug and that contains a
        `rag_db/` subfolder (or whose own directory is itself a ChromaDB
        store) is registered as a KB. The agent then names the KB it wants
        via the tool's `kb_slug` argument.

        Derived from `data_dir`, matching the backend's
        `knowledge_bases_dir` so the two services agree on layout out of
        the box.
        """
        return self.data_dir / "knowledge-bases"

    def require_s3_bucket(self) -> str:
        """ Return the output bucket or raise a `ToolError` if it isn't configured. """
        if not self.s3.bucket:
            raise ToolError(
                "No S3 bucket configured for HPC job output. Set "
                "VISTA_MCP_S3__BUCKET in env."
            )
        return self.s3.bucket

    def require_job_credentials(self) -> tuple[str, str, str]:
        """
        Return `(bucket, key_id, secret)` to hand the job for its output push,
        or raise a `ToolError` if any part is missing.

        Literal keys are required here even though Vista's own reads can fall
        back to an instance role: a compute node has no role. Checked before
        submission rather than at push time, because a job launched without
        them would run to completion and only then discover it cannot phone
        home, stranding its results where Vista has no way to reach them.
        """
        bucket = self.require_s3_bucket()
        if not (self.s3.key_id and self.s3.secret):
            raise ToolError(
                "No S3 credentials configured. Odo/Frontier jobs push their "
                "output to S3 themselves, and a compute node has no instance "
                "role, so set VISTA_MCP_S3__KEY_ID and VISTA_MCP_S3__SECRET in "
                "env (see aws/.env.sample for the IAM scope)."
            )
        return bucket, self.s3.key_id, self.s3.secret

    def job_key_prefix(self, cluster: str, job_id: str) -> str:
        """
        Key prefix holding one job's uploaded tree: `jobs/<cluster>/<job_id>`.

        The cluster is part of the key because Odo and Frontier are separate
        Slurm installs with independent job id counters sharing one bucket, so
        two live jobs can carry the same numeric id. Without it their trees
        would silently overwrite each other, and since the prefix is what
        authorizes a read, `get_hpc_job_outputs(job_id=..., cluster="odo")`
        would serve a moderate-enclave Frontier job's output to a caller
        holding only an open-enclave token.

        The `jobs/` root is fixed rather than configurable: it had one caller,
        was never overridden, and hardcoding it lets the IAM policy's
        `<bucket>/jobs/*` resource be exactly true. A deployment that needs its
        own namespace gets its own bucket.
        """
        return f"jobs/{cluster}/{job_id}"

    rag_model: str = "google/embeddinggemma-300m"

    hf_token: A[str | None, Field(validation_alias="HF_TOKEN")] = None

    metrics: MetricsSettings = Field(default_factory=MetricsSettings)
    """
    Metrics / instrumentation for the MCP server process (evaluation plan
    M3), mirroring the backend's `VISTA_BACKEND_METRICS__*` knobs. Defaults
    to `level=off` (byte-identical no-op). Override via
    `VISTA_MCP_METRICS__<FIELD>=...` env vars.
    """

    # ----- Experiment flags (evaluation plan §2a Group 3) -----------------
    # These change what the system *does* (not what it records), so they are
    # deliberately NOT metrics levels and must never be reachable by raising a
    # logging level. The M7 startup guard (step 9) refuses them when
    # VISTA_ENV=prod.
    hpc_dry_run: bool = False
    """
    When True, HPC job tools short-circuit S3M/IRI/S3 and return recorded
    synthetic responses (evaluation plan M6). No real cluster contact, no
    credentials required. Powers the E1/E6/E7b replay/concurrency/queue
    experiments. Default off — production behavior is unchanged.
    """

    hpc_queue_delay_s: float = 0.0
    """
    Synthetic queue wait for dry-run jobs (seconds). A dry-run job reports
    STATE=PENDING for this long after submission, then COMPLETED — letting
    E7b sweep 5 min / 1 h / 24 h queue behavior (polling overhead, idle-state
    memory, completion) without a real scheduler. Ignored unless
    `hpc_dry_run` is True.
    """

    s3: S3Settings = Field(default_factory=S3Settings)
    """
    S3 object store for HPC job output, overridable via
    `VISTA_MCP_S3__<FIELD>`. Required for Odo/Frontier submissions.
    """

    fault: FaultSettings = Field(default_factory=FaultSettings)
    """
    Fault-injection knobs (M7), overridable via `VISTA_MCP_FAULT__<FIELD>`.
    Inert by default; refused in prod by the startup guard.
    """

    def assert_experiment_flags_allowed(self) -> None:
        """
        Startup guard (M7, §2a Group 3): refuse to run with any
        behavior-changing experiment flag set when `VISTA_ENV=prod`. Called
        from `server.main()` so a misconfigured production deployment fails
        loudly at boot instead of silently faking jobs or injecting faults.
        """
        if self.env != 'prod':
            return
        offenders: list[str] = []
        if self.hpc_dry_run:
            offenders.append("VISTA_MCP_HPC_DRY_RUN")
        if self.hpc_queue_delay_s:
            offenders.append("VISTA_MCP_HPC_QUEUE_DELAY_S")
        if self.fault.is_active():
            offenders.append("VISTA_MCP_FAULT__*")
        if offenders:
            raise RuntimeError(
                "Experiment flags are forbidden when VISTA_ENV=prod: "
                f"{', '.join(offenders)}. These change what the system does "
                "(synthetic jobs / injected faults) and must never run in "
                "production. Unset them or run with VISTA_ENV=dev."
            )


settings = AppSettings()
if settings.hf_token:
    os.environ['HF_TOKEN'] = settings.hf_token
os.environ["HF_HOME"] = str(settings.data_dir / 'huggingface')
# Disable FastAPIs "Rich Logging" that makes it mangle and truncate errors from MCP tools.
os.environ['FASTMCP_ENABLE_RICH_LOGGING'] = 'false'
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
from fastmcp.utilities.logging import get_logger
# Show log messages sent via ctx.log (so they show up both as MCP logs and stderr logs)
get_logger(name="fastmcp.server.context.to_client").setLevel(level=logging.DEBUG)
