from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from pathlib import Path
import textwrap
from typing import Literal, Annotated as A
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

    # TODO: these are currently unused
    # allowed_uris: list[str] = [".*"]
    # """
    # List of regex patterns. A URI must match at least one to be allowed.
    # This is used to limit what files the display_file tool can render.
    # """
    #
    # uri_map: dict[str, str] = {
    #     "file:///mnt/data/output/": f"file://{Path('../data/output').resolve()}/",
    #     "file:///mnt/data/uploads/": f"file://{Path('../data/uploads').resolve()}/",
    # }
    # """
    # Mapping of URI prefixes to replacement prefixes, applied after allowed_uris checks.
    # Allows mapping paths inside the sandboxed container to paths outside for the display_file tool.
    # """

    mcp_apps_dir: ResolvedPath = Path(__file__).parent / 'mcp-apps'

    dockerfile: ResolvedPath = Path(__file__).parent / "docker/Dockerfile"
    image: str = "vista-sandbox"

    skills_dir: A[ResolvedPath, Field(validation_alias="VISTA_SKILLS_DIR")] = Path("../skills")
    output_dir: A[ResolvedPath, Field(validation_alias="VISTA_OUTPUT_DIR")] = Path("../data/output")
    uploads_dir: A[ResolvedPath, Field(validation_alias="VISTA_UPLOADS_DIR")] = Path("../data/uploads")
    volumes: list[tuple[ResolvedPath, Path, Literal['r', 'w']]] = [
        (Path("../skills"), Path("/mnt/skills"), 'r'),
        (Path("../data/output"), Path("/mnt/data/output"), 'w'),
        (Path("../data/uploads"), Path("/mnt/data/uploads"), 'w'),
    ]
    """
    List of volumes to mount into the sandbox as (host_path, sandbox_path, r/w) tuples
    """

    local_hpc_jobs_dir: ResolvedPath = Path("../hpc_jobs")
    remote_hpc_jobs_dir: Path = Path("/gpfs/wolf2/olcf/gen150/proj-shared/vista")
    """ Folder on the HPC cluster where the hpc_jobs will be copied. """
    hpc_account: str = "gen150-vista"
    """ Slurm account name for HPC job submission. """
    hpc_setup_script_template: str = ODO_SETUP_SCRIPT
    """
    Script sourced before every job script.

    This is a format string that can reference other config options like
    `remote_hpc_jobs_dir` and `session_id`.
    """

    def get_hpc_setup_script(self):
        """ The populated hpc_setup_script_template template """
        return self.hpc_setup_script_template.format(**self.model_dump())

    s3m_url: str = "https://amsc-open.s3m.olcf.ornl.gov"
    """ Base URL for the S3M API. """
    s3m_token: str | None = None
    """ Bearer token for S3M API authentication (VISTA_MCP_S3M_TOKEN env var). """
    s3m_resource: str = "odo"
    """ S3M compute resource id to submit jobs against. """

    hpc_ssh_host: CommaSeparatedList[str] = ["login1.odo.olcf.ornl.gov"]
    """
    SSH host for file access (SCP/sacct) on the HPC cluster.
    To use a jump host, pass an array or comma separated list of hosts.
    """
    hpc_ssh_user: str
    """ SSH user to log in as """

    session_id: A[str, Field(default_factory=lambda: f"{getpass.getuser()}-{datetime.now().strftime("%Y%m%dT%H%M%S")}-{uuid.uuid4().hex[:8]}")]
    """ Unique id for the Vista session """

    omd_url: str = "https://api.i2-core.american-science-cloud.org/mcp/openmetadata"
    omd_api_key: str | None = None
    """
    Key for the AmSC Open Metadata Server.
    Same key as the AmSC inference API, get it from https://api.i2-core.american-science-cloud.org
    """

    rag_db_path: ResolvedPath = Path("../rag_db")
    rag_model: str = "google/embeddinggemma-300m"


settings = AppSettings()
