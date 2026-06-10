import os, logging
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from pathlib import Path
from typing import Annotated as A
import getpass
import uuid
from datetime import datetime
from .lib.types import ResolvedPath, CommaSeparatedList

class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VISTA_MCP_",
        env_file=[p / '.env' for p in reversed([Path.cwd(), *Path.cwd().parents])],
        extra='ignore',
    )

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
    hpc_account: str = "gen150-vista"
    """
    OLCF project name used as the Slurm `account` for Odo jobs (one shared
    project for all Vista users). The user's S3M token must belong to this
    project. Frontier uses the per-user `frontier_account` field instead.
    """

    s3m_url: str = "https://amsc-open.s3m.olcf.ornl.gov"
    """
    Base URL for the OLCF AmSC IRI API on the open enclave (Odo / Defiant /
    Wombat / Quokka). Historically named `s3m_url` from the pre-IRI Odo path;
    Frontier uses the moderate-enclave equivalent (`olcf_iri_url`).
    """
    s3m_resource: str = "odo"
    """ OLCF compute resource group name (matched against the IRI discovery result). """
    odo_compute_resource_id: str = "70e0dde0-88e4-52e3-89f3-4849760f2e87"
    """
    Pinned IRI compute resource UUID for Odo. Bypasses `discover()` since the
    open-enclave service lists Odo / Defiant / Wombat / Quokka without a stable
    name/group match for `s3m_resource`. Look up via amscrot's `discover()` if
    OLCF rotates resource ids.
    """

    nersc_iri_url: str = "https://api.iri.nersc.gov"
    """ Base URL for the NERSC IRI API. """
    nersc_machine: str = "perlmutter"
    """ NERSC compute resource group name (used to match the IRI discovery result). """

    olcf_iri_url: str = "https://amsc-moderate.s3m.olcf.ornl.gov"
    """
    Base URL for the OLCF AmSC IRI API (moderate enclave — Frontier).
    The open-enclave host (amsc-open.s3m.olcf.ornl.gov) serves Odo/Defiant/Wombat/Quokka.
    """
    olcf_machine: str = "frontier"
    """ OLCF compute resource group name (used to match the IRI discovery result). """

    # Globus file-transfer config for Frontier (cluster="frontier"). The Vista
    # server hosts its own Globus collection (GCS or GCP) exposing `local_hpc_jobs_dir`
    # for source uploads and `output_dir` for output downloads; the user's per-record
    # Globus Auth + Transfer tokens authenticate as their OLCF identity for access to
    # `olcf_globus_collection_id`.
    vista_globus_collection_id: str | None = None
    """
    UUID of the Globus Collection hosted on the Vista server. Must expose the paths
    `local_hpc_jobs_dir` and `output_dir` (or a common ancestor). Required for Frontier
    file ops once the Globus pivot lands; empty during transition.
    """
    olcf_globus_collection_id: str = "36d521b3-c182-4071-b7d5-91db5d380d42"
    """
    UUID of the OLCF DTN (GCS5) Globus Collection that exposes Frontier's filesystem.
    Default is OLCF's current production DTN. Verify with the helper script at
    OLCF-Globus-Transfer/list_my_endpoints.py if OLCF rotates collections.
    """
    odo_globus_collection_id: str = "7399956e-a57b-4560-b3d7-a035ff42cad4"
    """
    UUID of the Globus Collection that exposes Odo's filesystem (open enclave;
    /gpfs/wolf2/olcf/gen150/... etc.). Distinct from the OLCF DTN used for
    Frontier — the two enclaves are reachable via different collections.
    """
    globus_native_app_client_id: str = "fae5c579-490a-4d76-b6eb-d78f65caeb63"
    """
    Globus Native App client UUID used to mint refresh-token authorizers from
    per-user refresh tokens. Default matches the client ID in
    OLCF-Globus-Transfer/get_olcf_token.py so refresh tokens minted by that script
    remain valid here.
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


    rag_model: str = "google/embeddinggemma-300m"

    hf_token: A[str | None, Field(validation_alias="HF_TOKEN")] = None


settings = AppSettings()
if settings.hf_token:
    os.environ['HF_TOKEN'] = settings.hf_token
# Disable FastAPIs "Rich Logging" that makes it mangle and truncate errors from MCP tools.
os.environ['FASTMCP_ENABLE_RICH_LOGGING'] = 'false'
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
from fastmcp.utilities.logging import get_logger
# Show log messages sent via ctx.log (so they show up both as MCP logs and stderr logs)
get_logger(name="fastmcp.server.context.to_client").setLevel(level=logging.DEBUG)
