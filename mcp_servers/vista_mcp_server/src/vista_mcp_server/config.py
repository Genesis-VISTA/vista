import os, sys, logging
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import BaseModel, Field
from pathlib import Path
from typing import Annotated as A, Literal
import getpass
import uuid
from datetime import datetime
from .lib.types import ResolvedPath, CommaSeparatedList, GlobusTokens
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
    to `odo_account` before Vista moves files for them with Globus
    """
    odo_globus_collection_id: str = "7399956e-a57b-4560-b3d7-a035ff42cad4"
    """
    UUID of the Globus Collection that exposes Odo's filesystem (open enclave)
    """
    odo_globus_refresh_token: str | None = None
    """
    Globus Transfer refresh token for Odo (open enclave) directory listings and
    `mkdir`, to work around the lack of IRI File API support.
    Generate with:
        ./scripts/get_globus_token.py --cluster odo --save-env
    """
    odo_globus_https_refresh_token: str | None = None
    """
    Refresh token for Odo's collection over the Globus HTTPS interface -- the
    one that moves bytes. A second token because Globus issues one per resource
    server and this one's is the collection UUID, not `transfer.api.globus.org`.
    Written by the same `--save-env` run as the Transfer token above.
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
    frontier_globus_collection_id: str = "36d521b3-c182-4071-b7d5-91db5d380d42"
    """
    UUID of the OLCF DTN Globus Collection that exposes Frontier's filesystem (moderate enclave).
    """
    frontier_globus_refresh_token: str | None = None
    """
    Deployment-wide Globus Transfer refresh token for Frontier (moderate
    enclave) directory listings and `mkdir`.
    Generate with:
        ./scripts/get_globus_token.py --cluster frontier --save-env
    """
    frontier_globus_https_refresh_token: str | None = None
    """
    Frontier's counterpart to `odo_globus_https_refresh_token`: the collection's
    own refresh token, which is what reads and writes file contents.
    """

    nersc_iri_url: str = "https://api.iri.nersc.gov"
    """ Base URL for the NERSC IRI API. """
    nersc_machine: str = "perlmutter"
    """ NERSC compute resource group name (used to match the IRI discovery result). """

    globus_native_app_client_id: str = "fae5c579-490a-4d76-b6eb-d78f65caeb63"
    """
    Globus Native App client UUID used to mint refresh-token authorizers from
    the deployment's Globus refresh token.
    """

    hpc_ssh_host: CommaSeparatedList[str] = ["login1.odo.olcf.ornl.gov"]
    """
    Legacy SSH host list, kept for the optional agenthpc subserver (disabled by
    default). The Odo/Frontier job tools no longer SSH — compute goes through
    IRI and file ops through Globus. To use a jump host, pass an array or comma
    separated list of hosts.
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

    def globus_tokens(self, cluster: Literal["odo", "frontier"]) -> GlobusTokens | None:
        """The deployment's Globus credential for a cluster, or None.

        The last of the three sources `UserConfig.require_globus_token` tries,
        and the only one a hosted deployment has ever had. Returning None rather
        than refusing, because this is a fallback: only the caller knows whether
        the two sources ahead of it also came up empty, and so only the caller
        can say to go and connect one.

        Both halves or neither. A deployment that has the Transfer token but not
        the collection's could list directories and read nothing, which looks
        like an empty output dir -- the exact confusion the HTTPS move exists to
        remove.
        """
        if cluster == "odo":
            transfer = self.odo_globus_refresh_token
            https = self.odo_globus_https_refresh_token
        else:
            transfer = self.frontier_globus_refresh_token
            https = self.frontier_globus_https_refresh_token
        if not (transfer and https):
            return None
        return GlobusTokens(transfer=transfer, https=https)

    embed_device: A[str | None, Field(validation_alias="VISTA_EMBED_DEVICE")] = None
    """
    Torch device for the query encoder, or `None` to let
    sentence-transformers choose the best available (cuda, then mps, then
    cpu).

    Unpinned because pinning cpu costs roughly 60x on a machine with an
    accelerator -- measured while indexing the molten-salt corpus. Set this
    to `cpu` to force it back. `build_rag.py` reads the same variable for
    the *indexing* encoder; the two need not agree, since the device
    affects only how fast vectors are computed and not their values.
    """

    rag_model: str = "microsoft/harrier-oss-v1-270m"
    """
    Sentence-transformers model used to encode `rag_search` queries.

    Ungated (MIT) and 640-dimension, so a fresh install needs no HuggingFace
    account. Kept byte-identical to `build_rag.TextRAG.__init__`'s
    `text_model` default, which is the *indexing* encoder: a Chroma
    collection locks to the dimension of its first insert, so the two names
    must never diverge. Change one, change the other.
    """

    rag_query_instruction: str = (
        "Given a question, retrieve passages from documents that answer it"
    )
    """
    One-sentence task description prepended to every `rag_search` query as
    `Instruct: <this>\\nQuery: `.

    `rag_model` is instruction-tuned. Its model card's FAQ: "Do I need to add
    instructions to the query? Yes, this is how the model is trained,
    otherwise you will see a performance degradation." Its
    `config_sentence_transformers.json` leaves `default_prompt_name` null, so
    sentence-transformers prepends nothing unless a caller asks; without this
    the queries went in bare.

    The same FAQ says the document side needs no instruction, and
    `build_rag.py` gives it none. That asymmetry is what makes this a
    query-time setting: changing it re-encodes no documents and invalidates
    no ChromaDB store.

    The default describes the task rather than any subject matter, because
    Knowledge Bases are user-built and may hold anything. It still beats the
    model card's stock `web_search_query` prompt, which describes retrieving
    web results for a search query -- what `rag_search` does is answer a
    question from an indexed document corpus. A deployment serving one
    known corpus can tighten this to name it.
    """

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
    When True, HPC job tools short-circuit S3M/IRI/Globus and return recorded
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
