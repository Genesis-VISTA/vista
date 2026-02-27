from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from typing import Literal
from .lib.types import ResolvedPath


class AppSettings(BaseSettings):
    allowed_uris: list[str] = [".*"]
    """ List of regex patterns. A URI must match at least one to be allowed. """

    uri_map: dict[str, str] = {}
    """ Mapping of URI prefixes to replacement prefixes, applied after allowed_uris checks. """

    mcp_apps_dir: ResolvedPath = Path(__file__).parent / 'mcp-apps'

    dockerfile: ResolvedPath = Path(__file__).parent / "docker/Dockerfile"
    image: str = "vista-sandbox"

    skills_dir: ResolvedPath = Path("../skills")
    volumes: list[tuple[ResolvedPath, Path, Literal['r', 'w']]] = [
        (Path("../skills"), Path("/mnt/skills"), 'r'),
    ]
    """
    List of volumes to mount into the sandbox as (host_path, sandbox_path, r/w) tuples
    """

    model_config = SettingsConfigDict(
        env_prefix="VISTA_MCP_",
        env_file="../.env",
        extra='ignore',
    )


settings = AppSettings()
