import os
from pathlib import Path
from typing import Annotated as A, Literal
from pydantic import AliasChoices, BaseModel, Field, ByteSize, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv, dotenv_values
import logging
from .utils.types import CommaSeparatedList, ResolvedPath, LogLevel
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


class ForumSettings(BaseModel):
    """
    Agent-forum settings — the git-backed debate forum (see
    `docs/forum-git-format.md` and `openspec/changes/forum-git-backend/`).

    Off by default: with `enabled=False` the service raises a clear error rather
    than running git, and a project's lab is off. With it on, the lab also needs
    a usable system git (`services/git_check.py`). Override via
    `VISTA_BACKEND_FORUM__ENABLED=true` etc.
    """

    enabled: bool = False

    git_binary: str = "git"
    """
    The git the forum drives; resolved on PATH unless an absolute path is given.

    The user's own git, not one VISTA ships, so pushes use the credentials they
    already have. The lab needs 2.34 or later and is off, with the reason shown,
    without it — see `services/git_check.py`.
    """

    push_retries: int = 5
    """
    Fetch-replay-push attempts before a publish gives up until the next sync.

    A rejected push means a peer posted first. Our posts are replayed on top of
    theirs and pushed again; they never conflict, so this only bounds a burst.
    """

    attachment_cap_bytes: int = 1_048_576
    """
    Largest attachment published with a post. Larger ones are truncated with a
    marker naming the full size and SHA-256, and kept whole on this install.
    Every peer downloads every attachment, so this is what bounds the repository.
    """

    repo_root: Path | None = None
    """
    The one repository a `ForumClient` instance works in. Not configuration.

    Filled in per project by `forum_config_for`; a `ForumSettings` read from the
    environment always leaves it `None`, and a client built on one refuses to run
    rather than guessing. `VISTA_BACKEND_FORUM__REPO_ROOT` no longer selects a
    deployment-wide forum — see `check_legacy_forum_env`, which says so out loud
    at boot rather than letting a stale line look like it still works.
    """

    timeout: float = 60.0
    """
    Per-command timeout in seconds for each git call. Local plumbing takes
    milliseconds; this bounds a fetch or push to a remote that has stopped
    answering.
    """

    max_job_wait_seconds: float = 1800.0
    """
    How long a role will wait for a simulation it commissioned.

    Named with its unit because it was set to `30` meaning half an hour and got
    half a minute. A bare `max_job_wait` invites that: minutes is the natural unit
    for a cluster job and seconds is what the code wants.

    A round used to end the moment a job was submitted, so the result arrived on
    the thread whenever it arrived — often after the verdict, where no agent ever
    reasoned about it. Waiting means the role that asked the question sees the
    answer, which is the point of asking.

    On timeout the debate carries on and the result is posted when it lands, so
    this bounds the stall rather than deciding whether the evidence is used.
    """

    job_poll_seconds: float = 15.0
    """
    How often a waiting role re-checks its job.

    The floor on noticing is `campaigns.monitor_interval` (300s by default), not
    this: the monitor is the only thing that polls the cluster, and a second
    poller would race it on the same rows. Lower that setting if a debate should
    see its results sooner.
    """

    default_rounds: int = 5
    """ Debate round budget when the caller does not specify one. """

    remote_url: str | None = None
    """
    Git URL this client's forum publishes to. Not configuration, like `repo_root`.

    Filled in per project from `Project.forum_repo_url`. Push access to that
    repository is the whole authorization model: anyone who can push can post
    under any identity they like. The forum's honesty is in labelling those
    posts `peer-claimed`, not in preventing them — so the repository's collaborator
    list is the security boundary, and it is the project's owners who should be
    choosing it, not whoever can edit a `.env`.
    """

    max_simulations: int = 2
    """
    HPC jobs one debate may commission, in total.

    A debate should test its sharpest prediction, not everything it wonders
    about. Past the cap the tool refuses and says so on the thread, so the limit
    is visible in the record rather than silently shaping the argument.
    """

    max_requests_per_turn: int = 12
    """
    Model requests one role may make in a single turn.

    A turn is a few tool calls and an answer, so this is generous. It exists to
    bound a tool loop: a model that keeps re-calling a tool whose answer does not
    help it will otherwise spend the whole budget and fail the debate. Kept
    explicit rather than inheriting pydantic-ai's default of 50, so the ceiling
    is a decision rather than a surprise.
    """


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
    def hpc_jobs_catalog(self) -> Path:
        """
        The `hpc_jobs/` catalog on disk, configured or derived.

        `hpc_jobs_dir` is the override and may be unset; this is what callers
        want. Two readers need it for different reasons — seeding writes the
        MSTDB CSV a job template needs, and a debate checks a job name exists
        before spending a submission to find out it does not — and resolving the
        fallback separately in each is how the two come to disagree.
        """
        return self.hpc_jobs_dir or Path(__file__).resolve().parents[3] / "hpc_jobs"

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

    @property
    def forum_git_dir(self) -> Path:
        """
        Root of the git-backed forum: this install's `host_id`, and per project
        (by id) a bare `repo.git` plus full copies of truncated attachments.

        Keyed by project id, not name, so renaming a project keeps its forum.
        The older `data/forums/` (h5i working repos) is not read by anything
        and can be deleted.
        """
        return self.data_dir / "forum-git"

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

    seed_science_projects: bool = False
    """
    Seed the `molten-salt` and `alloy-design` science projects, the
    `molten-salt-papers` knowledge base and the MSTDB-derived skill assets on first
    run from a `vista_data_token`. Set with `VISTA_BACKEND_SEED_SCIENCE_PROJECTS=true`.
    Off by default: VISTA's out-of-the-box project is AI Safety in Autonomous Labs.
    A bundled payload is seeded by what it contains and ignores this setting.
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

    forum: ForumSettings = Field(default_factory=ForumSettings)
    """
    Agent-forum (debate) settings; see `ForumSettings`. Disabled by default,
    so the backend runs unchanged without git installed.
    """

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


class HpcClusterSettings(BaseSettings):
    """
    Where each HPC cluster's facility, S3M, and Globus endpoints are, for the
    availability checks behind the NavRail's HPC cards.

    These belong to the MCP server, which is what actually submits jobs and
    moves files; the backend needs them only to ask the same services whether
    they would answer. So they are read under the MCP server's own names
    (`VISTA_MCP_ODO_IRI_URL`, ...) rather than `VISTA_BACKEND_*`: a deployment
    that points the MCP server somewhere else points these there too, because
    there is only one variable. The defaults are copies of
    `vista_mcp_server.config`'s, held in step by `tests/test_hpc_config_parity.py`.
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES, extra="ignore", env_prefix="VISTA_MCP_"
    )

    odo_iri_url: str = "https://amsc-open.s3m.olcf.ornl.gov"
    """ OLCF AmSC IRI API, open enclave. """
    odo_account: str = "gen150-vista"
    """ The OLCF project an Odo S3M token must belong to. """
    odo_compute_resource_id: str = "70e0dde0-88e4-52e3-89f3-4849760f2e87"
    """
    Odo's IRI compute resource. Matched by id, not name: the open enclave lists
    Odo beside Defiant, Wombat, and Quokka with no stable naming to match on.
    """
    odo_introspect_url: str = "https://s3m.olcf.ornl.gov/olcf/v1/token/ctls/introspect"
    odo_globus_collection_id: str = "7399956e-a57b-4560-b3d7-a035ff42cad4"
    odo_globus_refresh_token: SecretStr | None = None
    """ Deployment-wide Transfer token: the last Globus source, after the user's own. """
    odo_globus_https_refresh_token: SecretStr | None = None

    frontier_iri_url: str = "https://amsc-moderate.s3m.olcf.ornl.gov"
    """ OLCF AmSC IRI API, moderate enclave. """
    frontier_account: str = "chm243"
    """ The OLCF project a Frontier S3M token must belong to. """
    frontier_machine: str = "frontier"
    """ Frontier's resource name in the IRI status list, compared case-insensitively. """
    frontier_introspect_url: str = (
        "https://s3m.olcf.ornl.gov/olcf/v1/token/ctls/introspect"
    )
    frontier_globus_collection_id: str = "36d521b3-c182-4071-b7d5-91db5d380d42"
    frontier_globus_refresh_token: SecretStr | None = None
    frontier_globus_https_refresh_token: SecretStr | None = None

    nersc_iri_url: str = "https://api.iri.nersc.gov"
    nersc_machine: str = "perlmutter"
    """ The IRI status group Perlmutter's `compute` resource sits in. """

    lux_ssh_hosts: CommaSeparatedList[str] = [
        "hub.ccs.ornl.gov",
        "login1.lux.olcf.ornl.gov",
    ]
    """
    The SSH hops to Lux, hub first. Only the hub is probed: Lux has no IRI
    service, and the login node is reachable only through the hub.
    """
    lux_account: str = "stf218"
    """ The OLCF project Lux jobs run under. Shown on the card; not checked. """

    globus_native_app_client_id: str = "fae5c579-490a-4d76-b6eb-d78f65caeb63"
    """ The public client the users' Globus refresh tokens were minted for. """


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
hpc_settings = HpcClusterSettings.model_validate({})

os.environ["HF_HOME"] = str(settings.data_dir / "huggingface")

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s - %(levelname)s - %(message)s",
    force=True,
)
