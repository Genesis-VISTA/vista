import os, logging
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from pathlib import Path
import textwrap
from typing import Annotated as A
import getpass
import uuid
from datetime import datetime
from .lib.types import ResolvedPath, CommaSeparatedList

ODO_SETUP_SCRIPT = textwrap.dedent(r"""
    export VISTA_OUT="{remote_hpc_jobs_dir}/out/$SLURM_JOB_ID"
    mkdir -p -m 2775 "$VISTA_OUT"
    chmod 2775 "{remote_hpc_jobs_dir}" "{remote_hpc_jobs_dir}/out"

    export https_proxy="http://proxy.ccs.ornl.gov:3128";
    export http_proxy="http://proxy.ccs.ornl.gov:3128";
    export no_proxy="localhost,127.0.0.1,0.0.0.0";
""").strip()


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
    """ Slurm account name for HPC job submission. """
    hpc_setup_script_template: str = ODO_SETUP_SCRIPT
    """
    Script sourced before every job script.

    This is a format string referencing `{remote_hpc_jobs_dir}` (supplied per
    tool call via MCP metadata) and other settings.
    """

    s3m_url: str = "https://amsc-open.s3m.olcf.ornl.gov"
    """ Base URL for the S3M API. """
    s3m_resource: str = "odo"
    """ S3M compute resource id to submit jobs against. """

    nersc_iri_url: str = "https://api.iri.nersc.gov"
    """ Base URL for the NERSC IRI API. """
    nersc_machine: str = "perlmutter"
    """ NERSC compute resource group name (used to match the IRI discovery result). """

    hpc_ssh_host: CommaSeparatedList[str] = ["login1.odo.olcf.ornl.gov"]
    """
    SSH host for file access (SCP/sacct) on the HPC cluster.
    To use a jump host, pass an array or comma separated list of hosts.
    """
    hpc_ssh_user: str | None = None
    """ SSH user to log in as """

    session_id: A[str, Field(default_factory=lambda: f"{getpass.getuser()}-{datetime.now().strftime("%Y%m%dT%H%M%S")}-{uuid.uuid4().hex[:8]}")]
    """ Unique id for the Vista session """

    omd_url: str = "https://api.i2-core.american-science-cloud.org/mcp/openmetadata"
    omd_api_key: str | None = None
    """
    Key for the AmSC Open Metadata Server.
    Same key as the AmSC inference API, get it from https://api.i2-core.american-science-cloud.org
    """

    rag_db_path: ResolvedPath = Path("../../data/knowledge-bases/molten-salt-papers/rag_db")
    """
    Legacy single-KB ChromaDB directory the rag_search tool queries.
    Retained for backwards compatibility — when the multi-KB discovery
    under `knowledge_bases_dir` finds nothing, the rag_search tool falls
    back to this path under the slug "molten-salt-papers". Override with
    VISTA_MCP_RAG_DB_PATH (also read by the backend's molten_salt_rag_db
    setting).
    """

    knowledge_bases_dir: ResolvedPath = Path("../../data/knowledge-bases")
    """
    Root directory the MCP server scans at startup to discover the
    Knowledge Bases available to `rag_search`. Each immediate
    subdirectory whose name is a valid KB slug and that contains a
    `rag_db/` subfolder (or, for the legacy molten-salt layout, whose
    own directory is itself a ChromaDB store) is registered as a KB.
    The agent then names the KB it wants via the tool's `kb_slug`
    argument.

    Matches the backend's `VISTA_KNOWLEDGE_BASES_DIR` so the two
    services agree on layout out of the box.
    """

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
