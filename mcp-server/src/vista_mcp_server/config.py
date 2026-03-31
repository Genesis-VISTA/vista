from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from pathlib import Path
import textwrap
from typing import Literal, Annotated as A
import getpass
import uuid
from datetime import datetime
from .lib.types import ResolvedPath

FRONTIER_SETUP_SCRIPT = textwrap.dedent(r"""
    export VISTA_OUT="$VISTA_OUT/$SLURM_JOB_ID"
    mkdir -p "$VISTA_OUT"

    export https_proxy="http://proxy.ccs.ornl.gov:3128";
    export http_proxy="http://proxy.ccs.ornl.gov:3128";
    export no_proxy="localhost,127.0.0.1,0.0.0.0";
""").strip()


class AppSettings(BaseSettings):
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

    skills_dir: ResolvedPath = Path("../skills")
    output_dir: ResolvedPath = Path("../data/output")
    uploads_dir: ResolvedPath = Path("../data/uploads")
    volumes: list[tuple[ResolvedPath, Path, Literal['r', 'w']]] = [
        (Path("../skills"), Path("/mnt/skills"), 'r'),
        (Path("../data/output"), Path("/mnt/data/output"), 'w'),
        (Path("../data/uploads"), Path("/mnt/data/uploads"), 'w'),
    ]
    """
    List of volumes to mount into the sandbox as (host_path, sandbox_path, r/w) tuples
    """

    hpc_host: str = "frontier.olcf.ornl.gov"
    local_hpc_jobs_dir: ResolvedPath = Path("../hpc_jobs")
    remote_hpc_jobs_dir: Path = Path("/lustre/orion/stf218/proj-shared/vista/")
    """ Folder on the HPC cluster where the hpc_jobs will be copied. """
    remote_hpc_jobs_setup_script: str = FRONTIER_SETUP_SCRIPT
    """ Script to run before the setup of every job. """

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

    model_config = SettingsConfigDict(
        env_prefix="VISTA_MCP_",
        env_file=["./.env", "../.env"],
        extra='ignore',
    )


settings = AppSettings()
