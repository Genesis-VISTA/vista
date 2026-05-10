from pathlib import Path
from typing import Annotated as A
from pydantic import Field, ByteSize
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv
from .utils.types import ResolvedPath, LogLevel


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[p / '.env' for p in reversed([Path.cwd(), *Path.cwd().parents])],
        extra="ignore",
        env_prefix="VISTA_BACKEND_",
    )

    host: A[str, Field(validation_alias="VISTA_BACKEND_HOST")] = "127.0.0.1"
    port: A[int, Field(validation_alias="VISTA_BACKEND_PORT")] = 8001

    log_level: LogLevel = "INFO"

    model: str
    """
    LLM to use.
    
    This is passed to Pydantic AI, see https://pydantic.dev/docs/ai/api/pydantic-ai/providers/ for
    other env vars to set for specific providers
    """

    # TODO: Generalize this to allow multiple MCP servers
    mcp_url: str = Field(default="http://127.0.0.1:8000/mcp", validation_alias="VISTA_MCP_URL")

    skills_dir: A[ResolvedPath, Field(validation_alias="VISTA_SKILLS_DIR")] = Path("../skills")
    output_dir: A[ResolvedPath, Field(validation_alias="VISTA_OUTPUT_DIR")] = Path("../data/output")
    uploads_dir: A[ResolvedPath, Field(validation_alias="VISTA_UPLOADS_DIR")] = Path("../data/uploads")

    database_url: A[
        str,
        Field(default_factory=lambda: f"sqlite+aiosqlite:///{(Path.cwd() / '../vista.db').resolve()}"),
    ]
    """ SQLAlchemy async database URL. Defaults to a local SQLite db via aiosqlite. """

    max_upload_size: ByteSize = ByteSize(20 * 1024 * 1024)
    """ Size in bytes """


# Also load .env into the actual environ Pydantic AI will pick them up when making the model
for env_file in reversed(Settings.model_config['env_file']):
    load_dotenv(env_file, interpolate=False)

settings = Settings()
