import os, logging
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from .lib.types import ResolvedPath


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VISTA_DEV_MCP_",
        env_file=[p / '.env' for p in reversed([Path.cwd(), *Path.cwd().parents])],
        extra='ignore',
    )

    sandbox_mode: Literal["microsandbox", "container", "podman", "docker"] = "microsandbox"
    """
    Which sandbox backend to use.
    - `microsandbox` spins up a MicroVM via the microsandbox SDK
    - `container` runs commands inside a podman or docker container, whichever is available on the system.
    - `docker` runs commands inside a docker container.
    - `podman` runs commands inside a podman container.
    """

    dockerfile: ResolvedPath | None = Path(__file__).parent / "docker" / "Dockerfile"
    image: str = "vista-sandbox:latest"

    volumes: list[tuple[ResolvedPath, Path, Literal['r', 'w']]] = []
    """
    List of volumes to mount into the sandbox as (host_path, sandbox_path, r/w) tuples
    """

settings = AppSettings()

# Disable FastAPIs "Rich Logging" that makes it mangle and truncate errors from MCP tools.
os.environ['FASTMCP_ENABLE_RICH_LOGGING'] = 'false'
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
from fastmcp.utilities.logging import get_logger
# Show log messages sent via ctx.log (so they show up both as MCP logs and stderr logs)
get_logger(name="fastmcp.server.context.to_client").setLevel(level=logging.DEBUG)
