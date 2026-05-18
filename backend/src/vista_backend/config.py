from pathlib import Path
from typing import Annotated as A
from pydantic import Field, ByteSize, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv
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


# Also load .env into the actual environ Pydantic AI will pick them up when making the model.
#
# Diagnostic logging is deliberate here. Several users have hit a config
# where the chat path works (pydantic-ai falling back to a shell env
# var) but the indexer's citation extraction can't find OPENAI_API_KEY
# because the .env file in the cwd's chain didn't actually set it.
# We log: which .env paths we tried, which existed, and a key-shape
# summary of LLM-related env vars *after* loading. This is one of those
# things you only need to debug once but want unmissable when you do.
import logging as _logging
import os as _os

_dotenv_log = _logging.getLogger("vista.config")
_env_files_tried = list(reversed(Settings.model_config['env_file']))
_loaded_any = False
for env_file in _env_files_tried:
    exists = env_file.is_file()
    if exists:
        before = dict(_os.environ)
        load_dotenv(env_file, interpolate=False)
        new_keys = set(_os.environ) - set(before)
        # load_dotenv with override=False won't overwrite, so we only
        # see *newly added* keys. That's the right signal — it tells
        # us what this file actually contributed.
        _dotenv_log.warning(
            ".env loaded: path=%s added_keys=%s",
            env_file, sorted(new_keys) if new_keys else "<none-or-already-set>",
        )
        _loaded_any = True
    else:
        _dotenv_log.debug(".env not found at %s", env_file)

if not _loaded_any:
    _dotenv_log.warning(
        "No .env file was loaded from any of these locations: %s. "
        "If your LLM credentials live in one of these files, your "
        "indexer will not see them. Make sure backend/.env or the "
        "repo-root .env exists with OPENAI_API_KEY (or the Azure trio).",
        [str(p) for p in _env_files_tried],
    )

# Final summary of LLM-relevant env shape after loading. Same format
# as the indexer's snapshot for consistency. If chat works but
# indexing doesn't find credentials, this line vs. the indexer's
# line will tell you whether the env was mutated between startup
# and indexer time.
def _env_shape(name: str) -> str:
    v = _os.environ.get(name)
    if v is None:
        return f"{name}=<unset>"
    if not v:
        return f"{name}=<empty>"
    return f"{name}=<set,len={len(v)}>"

_dotenv_log.warning(
    "Post-dotenv LLM env shape: %s",
    ", ".join(_env_shape(n) for n in (
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
        "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_DEPLOYMENT_NAME",
        "VISTA_BACKEND_MODEL",
    )),
)

try:
    settings = Settings()
except ValidationError as exc:
    # Turn the pydantic blob into something a human can act on without
    # reading the source. The most common cause is a fresh checkout with
    # no .env yet — point at `.env.sample` and list the missing fields
    # by their actual env-var names.
    import os, sys
    missing: list[str] = []
    other: list[str] = []
    for err in exc.errors():
        if not err.get("loc"):
            other.append(err.get("msg", str(err)))
            continue
        field_name = str(err["loc"][0])
        field_info = Settings.model_fields.get(field_name)
        env_var: str | None = None
        if field_info is not None:
            alias = getattr(field_info, "validation_alias", None)
            if isinstance(alias, str):
                env_var = alias
        if env_var is None:
            env_var = f"VISTA_BACKEND_{field_name.upper()}"
        if err.get("type") == "missing":
            missing.append(f"{env_var} (field `{field_name}`)")
        else:
            other.append(f"{env_var}: {err.get('msg', '')}")

    lines = ["vista-backend failed to start: configuration is incomplete."]
    if missing:
        lines.append("")
        lines.append("Missing required environment variable(s):")
        for m in missing:
            lines.append(f"  - {m}")
    if other:
        lines.append("")
        lines.append("Other validation errors:")
        for o in other:
            lines.append(f"  - {o}")

    # Point at the sample, if we can find it.
    sample_candidates = [Path.cwd() / "../.env.sample", *[p / ".env.sample" for p in Path.cwd().parents]]
    sample_path = next((p for p in sample_candidates if p.is_file()), None)
    if sample_path is not None:
        lines.append("")
        lines.append(
            f"Copy {sample_path} to <repo>/.env and fill in the values."
        )
        lines.append(
            "For the typical AmSC setup, the relevant entries are "
            "VISTA_BACKEND_MODEL, OPENAI_BASE_URL, and OPENAI_API_KEY."
        )

    print("\n" + "\n".join(lines) + "\n", file=sys.stderr)
    sys.exit(2)
