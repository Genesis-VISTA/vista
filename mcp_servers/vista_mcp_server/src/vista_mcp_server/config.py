import os, sys, functools, logging
from fastmcp.exceptions import ToolError
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from pathlib import Path
from typing import Annotated as A, Literal
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
    Globus Transfer refresh token for Odo (open enclave) file ops to work around the lack of
    IRI File API support.
    Generate with:
        ./scripts/get_olcf_token.py --cluster odo --save-env
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
    Deployment-wide Globus Transfer refresh token for Frontier (moderate enclave) file ops.
    Generate with:
        ./scripts/get_olcf_token.py --cluster frontier --save-env
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

    @functools.cached_property
    def vista_globus_collection_id(self) -> str | None:
        """
        UUID of the Globus Collection hosted on the Vista server, read from the
        Globus Connect Personal config.
        """
        client_id_file = self.data_dir / "globusonline" / "lta" / "client-id.txt"
        if not client_id_file.exists():
            return None
        return client_id_file.read_text().strip() or None

    def require_globus_token(self, cluster: Literal["odo", "frontier"]) -> str:
        """ Return the Globus refresh token for the cluster or raise a `ToolError` if it isn't set. """
        if cluster == "odo":
            token = self.odo_globus_refresh_token
        else:
            token = self.frontier_globus_refresh_token
        if not token:
            raise ToolError(f"No Globus refresh token configured for '{cluster}' in env")
        return token

    rag_model: str = "google/embeddinggemma-300m"

    hf_token: A[str | None, Field(validation_alias="HF_TOKEN")] = None


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
