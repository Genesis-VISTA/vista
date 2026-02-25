from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class AppSettings(BaseSettings):
    allowed_uris: list[str] = [".*"]
    """ List of regex patterns. A URI must match at least one to be allowed. """

    uri_map: dict[str, str] = {}
    """ Mapping of URI prefixes to replacement prefixes, applied after allowed_uris checks. """

    mcp_apps_dir: Path = Path(__file__).parent / 'mcp-apps'

    model_config = SettingsConfigDict(
        env_prefix="VISTA_MCP_",
        env_file="../.env",
        extra='ignore',
    )


settings = AppSettings()
