import getpass, uuid, os, logging
from pathlib import Path
from datetime import datetime
from typing import Literal, Annotated as A

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .lib.types import ResolvedPath


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VISTA_MCP_",
        env_file=[p / '.env' for p in reversed([Path.cwd(), *Path.cwd().parents])],
        extra='ignore',
    )

    dockerfile: ResolvedPath = Path(__file__).parent / "docker" / "Dockerfile"
    image: str = "vista-sandbox"

    skills_dir: A[ResolvedPath, Field(validation_alias="VISTA_SKILLS_DIR")] = Path("../../skills")
    output_dir: A[ResolvedPath, Field(validation_alias="VISTA_OUTPUT_DIR")] = Path("../../data/output")
    uploads_dir: A[ResolvedPath, Field(validation_alias="VISTA_UPLOADS_DIR")] = Path("../../data/uploads")

    volumes: list[tuple[ResolvedPath, Path, Literal['r', 'w']]] = [
        (Path("../../skills"), Path("/mnt/skills"), 'r'),
        (Path("../../data/output"), Path("/mnt/data/output"), 'w'),
        (Path("../../data/uploads"), Path("/mnt/data/uploads"), 'w'),
    ]
    """
    List of volumes to mount into the sandbox as (host_path, sandbox_path, r/w) tuples
    """

    session_id: A[str, Field(default_factory=lambda: f"{getpass.getuser()}-{datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}")]
    """ Unique id for the Vista session """


settings = AppSettings()

# Disable FastAPIs "Rich Logging" that makes it mangle and truncate errors from MCP tools.
os.environ['FASTMCP_ENABLE_RICH_LOGGING'] = 'false'
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
from fastmcp.utilities.logging import get_logger
# Show log messages sent via ctx.log (so they show up both as MCP logs and stderr logs)
get_logger(name="fastmcp.server.context.to_client").setLevel(level=logging.DEBUG)
