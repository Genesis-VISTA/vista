import os
from pathlib import Path
from typing import Annotated as A, Literal
from pydantic import AliasChoices, BaseModel, Field, ByteSize, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv, dotenv_values
import logging
from .utils.types import ResolvedPath, LogLevel
from .vistaguard.config import VistaGuardSettings
from .metrics import MetricsSettings


class EmailSettings(BaseModel):
    """
    SMTP settings for outbound notifications (e.g. the campaign monitor emailing the
    user when a long-queued HPC job completes). Disabled by default; when off, the
    email service is a logged no-op. Override via `VISTA_BACKEND_EMAIL__HOST=...` etc.
    """

    enabled: bool = False
    host: str | None = None
    port: int = 587
    username: str | None = None
    password: SecretStr | None = None
    from_addr: str = "vista@localhost"
    use_tls: bool = True

    @property
    def is_configured(self) -> bool:
        return self.enabled and bool(self.host)


class CampaignSettings(BaseModel):
    """
    Multi-agent campaign settings. The background monitor (poll → notify → resume open
    HPC jobs) is opt-in; off by default so it doesn't poll the MCP server in dev/CI.
    Override via `VISTA_BACKEND_CAMPAIGNS__MONITOR_ENABLED=true` etc.
    """

    monitor_enabled: bool = False
    monitor_interval: float = 300.0


# Every `.env` from the filesystem root down to the cwd, nearest last so the
# most-specific file wins. Shared between the pydantic-settings config and the
# explicit `load_dotenv` pass below.
_ENV_FILES: list[Path] = [
    p / ".env" for p in reversed([Path.cwd(), *Path.cwd().parents])
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        extra="ignore",
        env_prefix="VISTA_BACKEND_",
        # Double underscore separates the parent field from the nested
        # field name in env vars, so the VistaGuardSettings sub-model
        # below is overridable as `VISTA_BACKEND_VISTAGUARD__ENABLED=...`.
        # Sibling top-level fields (those that don't contain `__` in
        # their env-var name) are unaffected by this setting.
        env_nested_delimiter="__",
    )

    env: A[Literal["dev", "prod"], Field(validation_alias="VISTA_ENV")] = "dev"

    host: str = "127.0.0.1"
    port: int = 8001

    log_level: LogLevel = "INFO"

    model: str = "openai:claude-sonnet"
    """
    LLM to use.
    
    This is passed to Pydantic AI, see https://pydantic.dev/docs/ai/api/pydantic-ai/providers/ for
    other env vars to set for specific providers

    Defaults to the AmSC-served `claude-sonnet` reached over the
    OpenAI-compatible endpoint in `openai_base_url`, so a fresh install has a
    working inference target with no configuration and only the access key is
    outstanding. Written with the `openai:` provider prefix to match
    `.env.sample` and the parsing in `utils/indexer.py:_parse_backend_model`,
    which keys the citation extractor off this same value.
    """

    openai_base_url: A[
        str,
        Field(
            validation_alias=AliasChoices(
                "VISTA_BACKEND_OPENAI_BASE_URL", "OPENAI_BASE_URL"
            )
        ),
    ] = "https://api.i2-core.american-science-cloud.org"
    """
    Base URL of the OpenAI-compatible inference endpoint. Defaults to the AmSC
    Inference API.

    Resolved here rather than left to the OpenAI SDK's own `OPENAI_BASE_URL`
    lookup, whose fallback is `api.openai.com` — a host where the default model
    does not exist. `agents/inference.py` passes this value explicitly when it
    builds the provider, so the endpoint no longer depends on a `.env` being
    present. The bare `OPENAI_BASE_URL` name is still accepted so existing
    deployments configured from `.env.sample` are unaffected.
    """

    openai_api_key: A[
        SecretStr | None,
        Field(
            validation_alias=AliasChoices(
                "VISTA_BACKEND_OPENAI_API_KEY", "OPENAI_API_KEY"
            )
        ),
    ] = None
    """
    Access key for `openai_base_url`.

    Optional: with no key configured every service still starts and the missing
    credential is reported when inference is first attempted. On a single-user
    install this is normally supplied through the settings UI instead, which
    takes precedence over this value.
    """

    mcp_url: str = Field(
        default="http://localhost:8000/mcp", validation_alias="VISTA_MCP_URL"
    )
    """ HTTP URL for the vista_mcp_server (HPC, RAG, display_file tools). """

    mcp_servers_path: A[
        ResolvedPath, Field(validation_alias="VISTA_MCP_SERVERS_PATH")
    ] = Path("../mcp_servers")
    """
    Path to the `mcp_servers/` directory in the repo. Used to locate per-agent STDIO
    MCP servers (e.g. `dev_mcp_server`) that the backend launches directly via `uv run`.
    """

    data_dir: A[ResolvedPath, Field(validation_alias="VISTA_DATA_DIR")] = Path(
        "../data"
    )
    """ Directory for data such as sandbox volumes and other created files """

    @property
    def knowledge_bases_dir(self) -> Path:
        """
        Root directory for Knowledge Bases. Each KB gets a subdirectory:
        `{knowledge_bases_dir}/{slug}/{pdfs/, rag_db/}`
        """
        return self.data_dir / "knowledge-bases"

    @property
    def storage_dir(self) -> Path:
        """
        Root directory for arbitrary stored files (e.g. skill folders). Entries
        are keyed by uuid; the owning DB row records the location as a path
        relative to `data_dir`.
        """
        return self.data_dir / "storage"

    database_url: A[
        str,
        Field(
            default_factory=lambda data: (
                f"sqlite+aiosqlite:///{(data['data_dir'] / 'vista.db').resolve()}"
            )
        ),
    ]
    """ SQLAlchemy async database URL. Defaults to a local SQLite db via aiosqlite. """

    max_upload_size: ByteSize = ByteSize(20 * 1024 * 1024)
    """ Size in bytes """

    encryption_key: A[
        SecretStr,
        Field(
            default_factory=lambda data: (
                "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
                if data["env"] == "dev"
                else None
            )
        ),
    ]
    """
    Fernet key for encrypting sensitive user token fields in the database.

    Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    Required in prod, defaults to a dummy key in dev.
    """

    github_token: str | None = None
    """
    Optional GitHub personal access token used when importing skills from
    private repos via `POST /skills/import`. Sent as the `Authorization: Bearer`
    header on requests to api.github.com. If unset, only public repos work.
    """

    vista_data_token: A[str | None, Field(validation_alias="VISTA_DATA_TOKEN")] = None
    """
    Optional GitLab personal access token, used to fetch private data. Needs Developer role and read_api and
    read_repository access. Generate at https://code.ornl.gov/v28/vista-data/-/settings/access_tokens
    """

    version: A[str, Field(validation_alias="VISTA_VERSION")] = "dev"
    """
    Human-readable build identifier, surfaced as the API's version.

    Set by the prebuilt package's launcher from the `VERSION` file beside it,
    so the same string appears in the artifact's manifest, in the launcher's
    output, and in `/openapi.json` from the running service -- which is how a
    researcher reporting a problem can say which build they have. `dev` on a
    checkout.
    """

    hpc_jobs_dir: A[
        ResolvedPath | None, Field(validation_alias="VISTA_HPC_JOBS_DIR")
    ] = None
    """
    Directory holding the HPC job templates, or `None` to derive it from the
    repository layout.

    Seeding drops the MSTDB CSV that `hpc_jobs/forge-tune` needs into this
    directory. It has to be configurable because the derived path is
    `db/seed.py`'s own location walked up five levels, which is correct only
    while `vista_backend` sits in `backend/src/`: installed non-editably -- as
    it is inside the prebuilt package -- that resolves inside the virtual
    environment, and the CSV would be written where nothing reads it. Point it
    at the same directory as the MCP server's
    `VISTA_MCP_LOCAL_HPC_JOBS_DIR`.
    """

    build_rag_dir: A[
        ResolvedPath | None, Field(validation_alias="VISTA_BUILD_RAG_DIR")
    ] = None
    """
    Directory containing `build_rag.py`, or `None` to derive it from the
    repository layout.

    Same reason as `hpc_jobs_dir`: `utils/indexer.py` locates the module by
    walking up from itself, which finds the repository root from
    `backend/src/` and the virtual environment's `lib/` from a non-editable
    install. Without this, indexing a knowledge base inside the prebuilt
    package fails with "Could not locate build_rag.py".
    """

    vista_data_payload_dir: A[
        ResolvedPath | None, Field(validation_alias="VISTA_DATA_PAYLOAD_DIR")
    ] = None
    """
    Optional directory holding an already-unpacked copy of the vista-data
    repository, used instead of `vista_data_token` to seed on first run.

    Set by the prebuilt package's launcher, which ships the payload rather than
    a token: a researcher gets the molten-salt corpus and the MSTDB assets with
    no access to `code.ornl.gov`. The layout is repo-relative and identical to
    what the GitLab client fetches -- `mstdb/...`, `molten-salt-papers/...` --
    because `LocalRepoClient` in `db/seed.py` is a drop-in for
    `GitlabRepoClient` and resolves the same paths against this root.

    Takes precedence over `vista_data_token` when both are set: a local payload
    is already on disk, so preferring it avoids a network fetch that could only
    produce the same files. Unset on a normal checkout, where seeding behaves
    exactly as before.
    """

    email: EmailSettings = Field(default_factory=EmailSettings)
    """ Outbound SMTP notification settings; see `EmailSettings`. Disabled by default. """

    campaigns: CampaignSettings = Field(default_factory=CampaignSettings)
    """ Multi-agent campaign settings (the background monitor); see `CampaignSettings`. """

    vistaguard: VistaGuardSettings = Field(default_factory=VistaGuardSettings)
    """
    VISTAGuard sidecar configuration. See `vista_backend.vistaguard.config`
    for the full set of fields. Every field defaults to off / minimal so
    that the default behavior of `Settings` is unchanged when VISTAGuard
    is not configured. Override individual fields via
    `VISTA_BACKEND_VISTAGUARD__<FIELD>=...` env vars.
    """

    metrics: MetricsSettings = Field(default_factory=MetricsSettings)
    """
    Metrics / instrumentation configuration (evaluation plan M1). Defaults
    to `level=off`, a byte-identical no-op. Override via
    `VISTA_BACKEND_METRICS__<FIELD>=...` env vars, e.g.
    `VISTA_BACKEND_METRICS__LEVEL=prod` for sampled production diagnostics
    or `LEVEL=perf` for the paper benchmarks.
    """


for env_file in reversed(_ENV_FILES):
    if Path(env_file).exists():
        values = dotenv_values(env_file)
        logging.info(
            f"Loaded from {Path(env_file).resolve()}: {', '.join(values.keys())}"
        )
        load_dotenv(env_file, interpolate=False)

# BaseSettings populates required fields (model, database_url, encryption_key)
# from env / dotenv sources; model_validate runs those sources without pyright
# demanding they be passed as constructor arguments.
settings = Settings.model_validate({})

os.environ["HF_HOME"] = str(settings.data_dir / "huggingface")

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s - %(levelname)s - %(message)s",
    force=True,
)
