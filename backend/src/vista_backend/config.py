import os
from pathlib import Path
from typing import Annotated as A, Literal
from pydantic import BaseModel, Field, ByteSize, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv, dotenv_values
import logging
from .utils.types import ResolvedPath, LogLevel
from palisade.config import PalisadeSettings
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
        # field name in env vars, so the PalisadeSettings sub-model
        # below is overridable as `VISTA_BACKEND_PALISADE__ENABLED=...`.
        # Sibling top-level fields (those that don't contain `__` in
        # their env-var name) are unaffected by this setting.
        env_nested_delimiter="__",
    )

    env: A[Literal["dev", "prod"], Field(validation_alias="VISTA_ENV")] = "dev"

    host: str = "127.0.0.1"
    port: int = 8001

    log_level: LogLevel = "INFO"

    model: str
    """
    LLM to use.
    
    This is passed to Pydantic AI, see https://pydantic.dev/docs/ai/api/pydantic-ai/providers/ for
    other env vars to set for specific providers
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

    email: EmailSettings = Field(default_factory=EmailSettings)
    """ Outbound SMTP notification settings; see `EmailSettings`. Disabled by default. """

    campaigns: CampaignSettings = Field(default_factory=CampaignSettings)
    """ Multi-agent campaign settings (the background monitor); see `CampaignSettings`. """

    palisade: PalisadeSettings = Field(default_factory=PalisadeSettings)
    """
    PALISADE sidecar configuration. See `vista_backend.palisade.config`
    for the full set of fields. Every field defaults to off / minimal so
    that the default behavior of `Settings` is unchanged when PALISADE
    is not configured. Override individual fields via
    `VISTA_BACKEND_PALISADE__<FIELD>=...` env vars.
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
