from pathlib import Path
from typing import Annotated as A
from pydantic import Field, ByteSize, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv, dotenv_values
import logging, os
from .utils.types import ResolvedPath, LogLevel


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[p / '.env' for p in reversed([Path.cwd(), *Path.cwd().parents])],
        extra="ignore",
        env_prefix="VISTA_BACKEND_",
    )

    host: str = "127.0.0.1"
    port: int = 8001

    log_level: LogLevel = "INFO"

    model: str
    """
    LLM to use.
    
    This is passed to Pydantic AI, see https://pydantic.dev/docs/ai/api/pydantic-ai/providers/ for
    other env vars to set for specific providers
    """

    # TODO: Generalize this to allow multiple MCP servers
    mcp_url: str = Field(default="http://localhost:8000/mcp", validation_alias="VISTA_MCP_URL")

    skills_dir: A[ResolvedPath, Field(validation_alias="VISTA_SKILLS_DIR")] = Path("../skills")
    output_dir: A[ResolvedPath, Field(validation_alias="VISTA_OUTPUT_DIR")] = Path("../data/output")
    uploads_dir: A[ResolvedPath, Field(validation_alias="VISTA_UPLOADS_DIR")] = Path("../data/uploads")

    knowledge_bases_dir: A[ResolvedPath, Field(validation_alias="VISTA_KNOWLEDGE_BASES_DIR")] = Path("../data/knowledge-bases")
    """
    Root directory for Knowledge Bases. Each KB gets a subdirectory:
    `{knowledge_bases_dir}/{slug}/{pdfs/, rag_db/}`. The builtin
    molten-salt KB lives under the same root so all KBs follow one
    consistent layout.
    """

    molten_salt_pdfs_dir: A[ResolvedPath, Field(validation_alias="VISTA_MCP_RAG_PDFS_PATH")] = Path("../data/knowledge-bases/molten-salt-papers/pdfs")
    """
    Source PDFs for the seeded molten-salt KB. Shared with build_rag.py;
    a PDF uploaded through the UI lands here so it ends up in the same
    corpus the MCP server queries. Points underneath `knowledge_bases_dir`
    so the builtin KB follows the same on-disk layout as user-created ones.
    """

    molten_salt_rag_db: A[ResolvedPath, Field(validation_alias="VISTA_MCP_RAG_DB_PATH")] = Path("../data/knowledge-bases/molten-salt-papers/rag_db")
    """
    ChromaDB directory for the seeded molten-salt KB. Shared with the
    `vista-mcp-server` rag_search tool — the env var is the same one
    the MCP server reads, so configuring it once configures both sides.
    """

    database_url: A[
        str,
        Field(default_factory=lambda: f"sqlite+aiosqlite:///{(Path.cwd() / '../vista.db').resolve()}"),
    ]
    """ SQLAlchemy async database URL. Defaults to a local SQLite db via aiosqlite. """

    max_upload_size: ByteSize = ByteSize(20 * 1024 * 1024)
    """ Size in bytes """

    github_token: str | None = None
    """
    Optional GitHub personal access token used when importing skills from
    private repos via `POST /skills/import`. Sent as the `Authorization: Bearer`
    header on requests to api.github.com. If unset, only public repos work.
    """


for env_file in reversed(Settings.model_config['env_file']):
    if Path(env_file).exists():
        values = dotenv_values(env_file)
        logging.info(f"Loaded from {Path(env_file).resolve()}: {', '.join(values.keys())}")
        load_dotenv(env_file, interpolate=False)

settings = Settings()
