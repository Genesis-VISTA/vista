"""
Data models / schemas
"""

import re
import uuid
from typing import Annotated as A, Any, Literal, Optional
from sqlalchemy import JSON, Column, String
from pydantic_ai import UsageLimits
from pydantic import BaseModel, TypeAdapter, field_validator
from sqlmodel import Field, SQLModel, UniqueConstraint
from ..utils.crypto import EncryptedStr

# TODO: Remove SQLModel and simplify the duplicate models


class ProjectBase(SQLModel):
    name: str
    description: str | None = None
    system_prompt: str | None = None
    skills: A[list[str], Field(default_factory=list, sa_column=Column(JSON))]
    """ List of skills available to this project """
    knowledge_bases: A[
        list[str], Field(default_factory=list, sa_column=Column(JSON))
    ]  # TODO should make this a foreign key later
    """
    Slugs of KnowledgeBases scoped to this project.

    Each entry must match a `KnowledgeBaseTable.slug`. At chat time the
    project's KB list is surfaced to the agent (via the system prompt)
    and passed through to the `rag_search` MCP tool's `kb_slug` argument,
    so a project that lists only one KB will only ever search that KB's
    corpus.
    """
    tools: A[list[str], Field(default_factory=list, sa_column=Column(JSON))]
    """
    List of tools available to this project
    
    These are wildcard matches, fnmatch style. Entries beginning with `!` are deny patterns;
    everything else is an allow pattern. A tool is allowed iff at least one allow pattern matches
    and no deny pattern matches. If there are no allow_patterns, assume allow "*".
    """
    forum_repo_url: str | None = None
    """
    Git remote the project's Hypothesis Lab publishes to, or None for no lab.

    The lab is per project because a forum is a *room*: who may post to it is who
    has push access to this repository, and that is a different set of people for
    every line of work. A single deployment-wide forum made one room for everyone
    and put the decision in a `.env` file, where the people who choose who is in
    the room cannot reach it.

    None — or blank — turns the Hypothesis Lab off for this project. That is the
    honest default: without somewhere to publish, a debate is a private argument
    with nobody to check it, and offering the feature would promise a peer review
    that cannot arrive.
    """

    usage_limits: A[dict, Field(default_factory=dict, sa_column=Column(JSON))]
    """
    Limits on the agent such as tool call depth
    Stored as a dict so we can store it as JSON in the DB, SQLModel won't parse the JSON into a
    UsageModel type directly.
    See pydantic_ai.UsageLimits for allowed values.
    """

    @field_validator("usage_limits", mode="after")
    @classmethod
    def _validate_usage_limits(cls, value):
        ta = TypeAdapter(UsageLimits)
        return ta.dump_python(ta.validate_python(value), mode="json")

    # TODO:
    # - Allow adding more MCP servers
    # - data such as manual uploads or documentation
    # - And link to datalake data
    # - And customize their rag database
    # - And we'll want project shared storage (either make uploads mounted per project or per chat or both)
    # - We might need to make it so that a project can configure things like the sandbox image used
    # - Maybe even customizing the inference model used
    # - Maybe make the system_prompt a template (jinja?)
    # - Should add limits on top of the configured Project usage_limits, and re-work how usage_limits is set up
    # - Projects need to have separate containers with different skills, upload dirs, etc.


# "table" models don't validate, so we have separate table, create, and public models.
# See https://sqlmodel.tiangolo.com/tutorial/fastapi/multiple-models/#the-herocreate-data-model
# TODO: Not convinced I like SQLModel, might go back to plain SQLAlchemy


class ProjectCreate(ProjectBase):
    """Project fields the user can set"""

    pass


class ProjectPublic(ProjectBase):
    """Project fields the user can read"""

    id: uuid.UUID


class ProjectTable(ProjectBase, table=True):
    """Project SQL model"""

    __tablename__: str = "project"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)


class ProjectMemberTable(SQLModel, table=True):
    """
    Membership join table. A row grants `user_id` read+write access to
    `project_id`. The composite primary key makes a (project, user) pair
    unique, so a duplicate add raises IntegrityError.
    """

    __tablename__: str = "project_member"
    project_id: uuid.UUID = Field(
        foreign_key="project.id", primary_key=True, ondelete="CASCADE"
    )
    user_id: uuid.UUID = Field(
        foreign_key="app_user.id", primary_key=True, ondelete="CASCADE"
    )


# ---------------------------------------------------------------------------
# Knowledge Base
#
# A Knowledge Base is a curated collection of PDFs that gets chunked,
# embedded, and indexed into a ChromaDB. The vector store powers the
# `rag_search` MCP tool, so any PDF added here becomes searchable from
# the chat agent.
#
# Publications are stored as a JSON column rather than a separate table.
# Per-KB write throughput is modest (a handful of rows touched per
# upload), row counts top out around ~hundreds for realistic corpora,
# and the JSON column matches the rest of this codebase's convention
# (cf. ProjectTable's `skills`, `tools`, `usage_limits`). If queryable
# publications become useful later, a follow-on migration to a separate
# table is straightforward.
# ---------------------------------------------------------------------------

# Lowercase letters, digits, and dashes only. 1–80 chars. Must start and
# end with an alphanumeric character. Matches the URL-safe slug convention
# used by Projects and Skills elsewhere in the codebase.
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$")


def is_valid_slug(slug: str) -> bool:
    return bool(_SLUG_RE.match(slug))


PublicationIndexStatus = Literal[
    "unindexed",  # PDF on disk, never chunked
    "queued",  # registered for indexing, indexer hasn't started this row yet
    "indexing",  # indexer is actively working on this row
    "indexed",  # text chunks (and possibly citation row) are in chroma
    "failed",  # indexing was attempted and raised; see index_error
]


PublicationCitationStatus = Literal[
    "pending",  # citation extraction hasn't been attempted yet
    "extracted",  # LLM returned a parsed citation; metadata fields are populated
    "skipped",  # the PDF was already in chroma; citation reused from prior run
    "failed",  # LLM call ran but produced an error or unparseable output
    "disabled",  # no LLM credentials configured, citation extraction is off
]


class Publication(BaseModel):
    """
    A single paper inside a Knowledge Base.

    All citation fields are optional — citation extraction can fail or
    be incomplete, in which case the publication is still listed but
    with `None` fields. `filename` is the canonical identifier within
    the KB (relative to the KB's pdfs/ directory).
    """

    filename: str
    title: Optional[str] = None
    authors: Optional[list[str]] = None
    abstract: Optional[str] = None
    journal: Optional[str] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None
    year: Optional[str] = None
    doi: Optional[str] = None
    keywords: Optional[list[str]] = None
    publisher: Optional[str] = None
    size: int = 0
    """ Bytes on disk for the PDF; 0 when the PDF isn't on disk. """
    added_at: str = ""
    """ ISO timestamp when this publication was added to the KB. """
    has_pdf: bool = True
    """
    Whether the source PDF for this publication is currently on disk.
    False for entries reconstructed from a built ChromaDB whose source
    PDF is no longer in the KB's pdfs/ directory — those are still
    searchable via rag_search but cannot be opened or re-extracted.
    """
    index_status: PublicationIndexStatus = "unindexed"
    index_error: Optional[str] = None
    indexed_at: Optional[str] = None
    citation_status: PublicationCitationStatus = "pending"
    """
    Distinct from `index_status`. The indexer always tries text chunks
    first; citation metadata is best-effort on top. A publication can be
    fully indexed (chunks searchable in chroma) while citation extraction
    failed or was disabled — the UI surfaces these states separately so
    a missing citation doesn't get conflated with "indexer still running".
    """
    citation_error: Optional[str] = None
    """Last error from the citation LLM call, if citation_status='failed'."""


KnowledgeBaseBuildStatus = Literal[
    "pending",  # chroma collections empty / missing
    "ready",  # collections populated; ready to serve search
    "stale",  # new PDFs added since the last successful index
    "failed",  # last indexing attempt raised
]


class KnowledgeBaseBase(SQLModel):
    """
    Fields shared by all Knowledge Base model variants.
    """

    slug: str
    """ URL-safe identifier; immutable once created. """

    name: str
    description: str | None = None

    pdfs_dir: str
    """
    Absolute filesystem path to the directory holding source PDFs.
    Written to `{knowledge_bases_dir}/{slug}/pdfs`.
    """

    rag_db_path: str
    """
    Absolute filesystem path to the ChromaDB persist directory.
    """

    shared_with_mcp: bool = False
    """
    True when pdfs_dir/rag_db_path coincide with the MCP server's
    configured rag corpus. UI surfaces this so the user understands
    that uploads here will show up in chat tool calls (after the
    indexer finishes), vs being isolated to this KB only.
    """

    publications: A[
        list[Publication], Field(default_factory=list, sa_column=Column(JSON))
    ]
    build_status: A[
        KnowledgeBaseBuildStatus,
        Field(
            default="pending",
            sa_column=Column(String, nullable=False, default="pending"),
        ),
    ]
    last_built_at: str | None = None
    """ ISO timestamp of the most recent successful indexer run. """


class KnowledgeBaseCreate(SQLModel):
    """
    Fields the user can set when creating a Knowledge Base via the API.
    Subset of KnowledgeBaseBase — path fields are derived server-side
    from the slug, and publications are populated by separate POSTs
    against /knowledge-bases/{slug}/publications.
    """

    slug: str
    name: str
    description: str | None = None


class KnowledgeBaseUpdate(SQLModel):
    """Fields editable on an existing KB."""

    name: str | None = None
    description: str | None = None


class KnowledgeBasePublic(KnowledgeBaseBase):
    id: uuid.UUID
    created_at: str
    updated_at: str


class KnowledgeBaseTable(KnowledgeBaseBase, table=True):
    """Knowledge Base SQL model."""

    __tablename__: str = "knowledge_base"
    __table_args__ = (UniqueConstraint("slug", name="uq_knowledge_base_slug"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: str = Field(default="")
    """ ISO timestamp set at row insert time. """
    updated_at: str = Field(default="")
    """ ISO timestamp updated on every write. """


class IndexProgress(BaseModel):
    """
    Snapshot of an in-flight indexing run for a single KB. Tracked
    in-process in `api.knowledge_bases._INDEX_PROGRESS` and surfaced
    over the wire as a sidecar on KB read responses; not persisted to
    the DB.
    """

    phase: Literal["loading_model", "indexing", "done"]
    sub_phase: Optional[Literal["starting", "citation", "chunks", "done"]] = None
    processed: int
    total: int
    current: Optional[str] = None
    started_at: float
    """ Unix epoch seconds. """


# Skills
# Note, this is different than the models in agents/skills.py. agents/skills.py only handles the
# skills spec directly (https://agentskills.io/specification). This database model handles storing
# to location on disk, and some ownership metadata, in addition to mirroring the Skill spec metadata
#
# TODO: Currently skills are still globably editable, and project just select a subset of skills.
# We need to make skills scoped to a project, only editable by their creator, etc.


class SkillBase(SQLModel):
    name: str
    """
    SKILL.md name. Must be kebab case. Unique
    """

    # Mirrored AgentSkills spec metadata (synced from SKILL.md). The on-disk
    # SKILL.md is the source of truth; these columns are kept in sync on
    # create/patch so the full spec is readable without touching disk.
    description: str
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: str | None = None
    skill_metadata: A[
        dict[str, str | list[str]] | None,
        Field(default=None, sa_column=Column(JSON), serialization_alias="metadata"),
    ]
    """
    The spec's client-specific `metadata` dict. The column is named
    `skill_metadata` because `metadata` is reserved on SQLAlchemy declarative
    models, but the API exposes it as `metadata` (matching create/patch input).
    """

    # DB-only hub metadata (never written to SKILL.md).
    author: str | None = None
    repo_url: str | None = None
    is_public: bool = False


class SkillPublic(SkillBase):
    id: uuid.UUID
    created_at: str
    updated_at: str


class SkillTable(SkillBase, table=True):
    """Skill SQL model."""

    __tablename__: str = "skill"
    __table_args__ = (UniqueConstraint("name", name="uq_skill_name"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    path: str
    """ The skill's folder on disk, relative to `settings.data_dir` """
    created_at: str = Field(default="")
    """ ISO timestamp set at row insert time. """
    updated_at: str = Field(default="")
    """ ISO timestamp updated on every write. """


class SkillUpdate(BaseModel):
    """
    Fields editable on an existing skill via PATCH.
    """

    description: str | None = None
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: str | None = None
    metadata: dict[str, str | list[str]] | None = None
    author: str | None = None
    repo_url: str | None = None
    is_public: bool | None = None


HpcCluster = Literal["frontier", "odo", "perlmutter", "lux"]
"""The clusters the NavRail can show an availability card for."""


def _dedupe_clusters(v: list[HpcCluster] | None) -> list[HpcCluster] | None:
    return None if v is None else list(dict.fromkeys(v))


class UserBase(SQLModel):
    pass


# Treat "" the same as None for the optional config fields so a cleared frontend
# input doesn't end up as a non-null-but-empty token/account in the DB
_USER_CONFIG_NULLABLE_FIELDS = (
    "inference_model",
    "inference_base_url",
    "inference_api_key",
    "nersc_account",
    "nersc_remote_dir",
    "frontier_account",
    "frontier_remote_dir",
    "odo_s3m_token",
    "frontier_s3m_token",
    "nersc_iri_token",
    "globus_token",
    "odo_globus_token",
    "frontier_globus_token",
    "globus_https_token",
    "odo_globus_https_token",
    "frontier_globus_https_token",
)


def _empty_str_to_none(v):
    return None if v == "" else v


class UserCreate(UserBase):
    email: str
    is_admin: bool = False
    remote_hpc_jobs_dir: str = "/gpfs/wolf2/olcf/gen150/proj-shared/vista"
    inference_model: str | None = None
    inference_base_url: str | None = None
    inference_api_key: str | None = None
    nersc_account: str | None = None
    nersc_remote_dir: str | None = None
    frontier_account: str | None = None
    frontier_remote_dir: str | None = None
    odo_s3m_token: str | None = None
    frontier_s3m_token: str | None = None
    nersc_iri_token: str | None = None
    globus_token: str | None = None
    odo_globus_token: str | None = None
    frontier_globus_token: str | None = None
    globus_https_token: str | None = None
    odo_globus_https_token: str | None = None
    frontier_globus_https_token: str | None = None

    @field_validator(*_USER_CONFIG_NULLABLE_FIELDS, mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        return _empty_str_to_none(v)


class UserUpdate(UserBase):
    is_admin: bool | None = None
    remote_hpc_jobs_dir: str | None
    inference_model: str | None = None
    inference_base_url: str | None = None
    inference_api_key: str | None = None
    nersc_account: str | None = None
    nersc_remote_dir: str | None = None
    frontier_account: str | None = None
    frontier_remote_dir: str | None = None
    odo_s3m_token: str | None = None
    frontier_s3m_token: str | None = None
    nersc_iri_token: str | None = None
    globus_token: str | None = None
    odo_globus_token: str | None = None
    frontier_globus_token: str | None = None
    globus_https_token: str | None = None
    odo_globus_https_token: str | None = None
    frontier_globus_https_token: str | None = None
    hpc_hidden_clusters: list[HpcCluster] | None = None
    """ Clusters left out of the NavRail. `null` or `[]` shows them all. """

    @field_validator(*_USER_CONFIG_NULLABLE_FIELDS, mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        return _empty_str_to_none(v)

    @field_validator("hpc_hidden_clusters")
    @classmethod
    def _dedupe_hidden(cls, v):
        return _dedupe_clusters(v)


class UserSelfUpdate(UserBase):
    remote_hpc_jobs_dir: str | None = None
    inference_model: str | None = None
    inference_base_url: str | None = None
    inference_api_key: str | None = None
    nersc_account: str | None = None
    nersc_remote_dir: str | None = None
    frontier_account: str | None = None
    frontier_remote_dir: str | None = None
    odo_s3m_token: str | None = None
    frontier_s3m_token: str | None = None
    nersc_iri_token: str | None = None
    globus_token: str | None = None
    odo_globus_token: str | None = None
    frontier_globus_token: str | None = None
    globus_https_token: str | None = None
    odo_globus_https_token: str | None = None
    frontier_globus_https_token: str | None = None
    hpc_hidden_clusters: list[HpcCluster] | None = None
    """ Clusters left out of the NavRail. `null` or `[]` shows them all. """

    @field_validator(*_USER_CONFIG_NULLABLE_FIELDS, mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        return _empty_str_to_none(v)

    @field_validator("hpc_hidden_clusters")
    @classmethod
    def _dedupe_hidden(cls, v):
        return _dedupe_clusters(v)


class UserPublic(UserBase):
    id: uuid.UUID
    email: str
    is_admin: bool = False
    hpc_hidden_clusters: list[str] = []
    """
    Clusters left out of the NavRail; not a secret, so on the light view too.
    Plain strings on the way out: only writes are checked against
    `HpcCluster`, so a stored name that later leaves the list cannot make
    reading the user fail.
    """

    @field_validator("hpc_hidden_clusters", mode="before")
    @classmethod
    def _null_is_none_hidden(cls, v):
        return [] if v is None else v


class UserPublicWithConfig(UserBase):
    """Full user view for the authenticated user — includes decrypted token fields."""

    id: uuid.UUID
    email: str
    is_admin: bool = False
    remote_hpc_jobs_dir: str = "/gpfs/wolf2/olcf/gen150/proj-shared/vista"
    inference_model: str | None = None
    inference_base_url: str | None = None
    inference_api_key: str | None = None
    nersc_account: str | None = None
    nersc_remote_dir: str | None = None
    frontier_account: str | None = None
    frontier_remote_dir: str | None = None
    odo_s3m_token: str | None = None
    frontier_s3m_token: str | None = None
    nersc_iri_token: str | None = None
    globus_token: str | None = None
    odo_globus_token: str | None = None
    frontier_globus_token: str | None = None
    globus_https_token: str | None = None
    odo_globus_https_token: str | None = None
    frontier_globus_https_token: str | None = None
    hpc_hidden_clusters: list[str] = []
    """
    Clusters left out of the NavRail; not a secret, so on the light view too.
    Plain strings on the way out: only writes are checked against
    `HpcCluster`, so a stored name that later leaves the list cannot make
    reading the user fail.
    """

    @field_validator("hpc_hidden_clusters", mode="before")
    @classmethod
    def _null_is_none_hidden(cls, v):
        return [] if v is None else v


class UserTable(SQLModel, table=True):
    __tablename__: str = "app_user"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    email: str = Field(unique=True)
    is_admin: bool = False
    remote_hpc_jobs_dir: str = "/gpfs/wolf2/olcf/gen150/proj-shared/vista"
    """ Folder on the HPC cluster (Odo) where hpc_jobs will be copied. """
    inference_model: str | None = None
    """
    Chat model for this user, as `provider:name`. Overrides
    `Settings.model` when set. Not a secret -- stored in the clear.
    """
    inference_base_url: str | None = None
    """
    OpenAI-compatible endpoint for this user. Overrides
    `Settings.openai_base_url` when set. Not a secret.
    """
    inference_api_key: str | None = Field(
        default=None, sa_column=Column(EncryptedStr, nullable=True)
    )
    """
    Access key for `inference_base_url`. Encrypted at rest.

    On a single-user install this row *is* the deployment configuration: it is
    where a researcher's key lands when they paste it into the settings modal,
    and it takes precedence over `Settings.openai_api_key`. Changing it evicts
    this user's pooled agents through `update_user`'s `invalidate_agents` call,
    so the next message picks it up with no restart.
    """
    nersc_account: str | None = None
    """ NERSC project account for Slurm submission. """
    nersc_remote_dir: str | None = None
    """ Absolute remote dir on the NERSC machine (e.g. /pscratch/sd/<u>/<user>/.vista). Required for Perlmutter. """
    frontier_account: str | None = None
    """
    OLCF project name used as the Slurm `--account` for Frontier submissions
    (e.g. "chm243"). Must match the `project` claim on the user's S3M token,
    since the IRI service submits Slurm jobs as <project>_auser. Required for
    cluster="frontier".
    """
    frontier_remote_dir: str | None = None
    """
    Folder on Frontier where hpc_jobs will be copied (e.g.
    /lustre/orion/<project>/proj-shared/vista). Required for cluster="frontier";
    must be writable by the user's Frontier project (typically different from
    the Odo proj-shared dir).
    """
    s3m_token: str | None = Field(
        default=None, sa_column=Column(EncryptedStr, nullable=True)
    )
    """
    Legacy single S3M token. Nothing reads it: an S3M token is scoped to one
    OLCF project, so one field could only ever authorize one of Odo and
    Frontier. Kept only because SQLite column drops are not worth it; see
    `odo_s3m_token` / `frontier_s3m_token`.
    """
    odo_s3m_token: str | None = Field(
        default=None, sa_column=Column(EncryptedStr, nullable=True)
    )
    """
    S3M bearer token for Odo (open enclave), minted in Odo's OLCF project.
    Authorizes Odo job submission through IRI and gates Odo file operations.
    Encrypted at rest.
    """
    frontier_s3m_token: str | None = Field(
        default=None, sa_column=Column(EncryptedStr, nullable=True)
    )
    """
    S3M bearer token for Frontier (moderate enclave), minted in
    `frontier_account`'s project. Encrypted at rest.
    """
    nersc_iri_token: str | None = Field(
        default=None, sa_column=Column(EncryptedStr, nullable=True)
    )
    """
    Globus access token for NERSC IRI. Encrypted at rest. Expires ~48h.
    Refresh: python iri-api-get-globus-token-main/get_globus_token.py --refresh-only
    """
    globus_token: str | None = Field(
        default=None, sa_column=Column(EncryptedStr, nullable=True)
    )
    """
    Globus Transfer refresh token used for OLCF directory listings and `mkdir`
    when no cluster-specific one is set. Long-lived; the MCP server mints
    short-lived access tokens from it on each submission via
    `globus_sdk.RefreshTokenAuthorizer`. Encrypted at rest.
    """
    odo_globus_token: str | None = Field(
        default=None, sa_column=Column(EncryptedStr, nullable=True)
    )
    """
    Odo's Globus Transfer refresh token. Encrypted at rest. Separate from
    Frontier's because the two enclaves authenticate against different SSO
    domains -- opensso.ccs.ornl.gov and sso.ccs.ornl.gov -- and can be
    different identities, so one token cannot be assumed to authorize the
    other's file operations.
    """
    frontier_globus_token: str | None = Field(
        default=None, sa_column=Column(EncryptedStr, nullable=True)
    )
    """ Frontier's Globus Transfer refresh token. Encrypted at rest. """
    globus_https_token: str | None = Field(
        default=None, sa_column=Column(EncryptedStr, nullable=True)
    )
    """
    The shared counterpart to `globus_token`: a refresh token for the OLCF
    collection itself, over the Globus HTTPS interface. Encrypted at rest.

    A second token per credential because Globus issues one per resource
    server, and file *contents* belong to the collection rather than to
    Transfer. Both halves are needed: the Transfer token lists a directory, and
    this one reads what is in it. See `lib/globus.py` in the MCP server.
    """
    odo_globus_https_token: str | None = Field(
        default=None, sa_column=Column(EncryptedStr, nullable=True)
    )
    """ Odo collection's HTTPS refresh token. Encrypted at rest. """
    frontier_globus_https_token: str | None = Field(
        default=None, sa_column=Column(EncryptedStr, nullable=True)
    )
    """ Frontier collection's HTTPS refresh token. Encrypted at rest. """
    hpc_hidden_clusters: list[str] | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    """
    Clusters this user has turned off in the NavRail, whose availability is
    then never checked. Stored as what is *hidden* so that a cluster added
    later shows for everyone by default, with nothing to migrate.
    """


# ---------------------------------------------------------------------------
# Chat sessions
# ---------------------------------------------------------------------------

ChatMessageRole = Literal["user", "assistant", "system", "tool"]


class ChatTranscriptMessage(BaseModel):
    """
    Minimal persisted chat bubble for the UI.
    """

    id: str
    role: ChatMessageRole
    content: str
    intermediate: bool | None = None


class ChatSessionBase(SQLModel):
    """
    Persisted chat state for a single conversation inside a project.
    """

    title: str = "New conversation"
    message_history: A[
        list[dict[str, Any]], Field(default_factory=list, sa_column=Column(JSON))
    ]
    messages: A[
        list[ChatTranscriptMessage], Field(default_factory=list, sa_column=Column(JSON))
    ]
    latest_result: A[
        dict[str, Any] | None,
        Field(default=None, sa_column=Column(JSON, nullable=True)),
    ]


class ChatSessionCreate(BaseModel):
    title: str | None = None


class ChatSessionUpdate(BaseModel):
    title: str | None = None
    message_history: list[dict[str, Any]] | None = None
    messages: list[ChatTranscriptMessage] | None = None
    latest_result: dict[str, Any] | None = None


class ChatSessionSummary(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    project_id: uuid.UUID
    title: str
    created_at: str
    updated_at: str


class ChatSessionPublic(ChatSessionBase):
    id: uuid.UUID
    user_id: uuid.UUID
    project_id: uuid.UUID
    created_at: str
    updated_at: str


class ChatSessionTable(ChatSessionBase, table=True):
    __tablename__: str = "chat_session"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="app_user.id", ondelete="CASCADE")
    project_id: uuid.UUID = Field(foreign_key="project.id", ondelete="CASCADE")
    created_at: str = Field(default="")
    updated_at: str = Field(default="")


# ---------------------------------------------------------------------------
# Multi-agent campaigns
#
# A campaign is one long-running run of the planner + subagents workflow (see
# docs/multi-agent-framework.md). The backend is domain-agnostic: a campaign's
# behaviour comes entirely from the planner skill named in `planner_skill` and
# its `campaign.yaml` manifest. These tables only persist the durable run state
# so the workflow survives a backend restart and resumes from where it stopped:
#
#   CampaignRun  — the run: its agreed spec, the editable plan, and status.
#   CampaignStep — one unit of work in a cycle (a subagent order, or a
#                  planner `decision`), with its parsed result.
#   HpcJob       — a submitted HPC job, linked to the step that launched it.
#                  The backend's durable record of in-flight jobs the monitor
#                  polls; complements the MCP server's own job registry.
# ---------------------------------------------------------------------------

CampaignStatus = Literal[
    "gathering",  # eliciting inputs from the user (Phase A)
    "planning",  # drafting / awaiting plan approval (Phase B)
    "running",  # a cycle's jobs are in flight (Phase C)
    "awaiting_user",  # paused on a user decision (Phase D/E)
    "converged",  # goal met (Phase G)
    "exited",  # user-confirmed exit (Phase G)
]


class CampaignRunBase(SQLModel):
    domain: str
    """ Campaign/domain key, from the planner skill's `campaign.yaml` (e.g. "splash"). """
    planner_skill: str
    """ Name of the planner skill providing the playbook + manifest; reloaded on resume. """
    title: str | None = None
    """ Human-friendly campaign title; the planner may set this once the goal is known. """
    spec: A[dict[str, Any], Field(default_factory=dict, sa_column=Column(JSON))]
    """ The agreed campaign spec: variables, ranges, metric targets, constraints, platform, budget. """
    plan: A[list[dict[str, Any]], Field(default_factory=list, sa_column=Column(JSON))]
    """ The editable, user-facing numbered plan (the source of truth the user can amend). """
    status: A[
        CampaignStatus,
        Field(
            default="gathering",
            sa_column=Column(String, nullable=False, default="gathering"),
        ),
    ]


class CampaignRunPublic(CampaignRunBase):
    id: uuid.UUID
    project_id: uuid.UUID
    user_id: uuid.UUID
    session_id: uuid.UUID | None = None
    created_at: str
    updated_at: str


class CampaignRunTable(CampaignRunBase, table=True):
    __tablename__: str = "campaign_run"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    project_id: uuid.UUID = Field(foreign_key="project.id", ondelete="CASCADE")
    user_id: uuid.UUID = Field(foreign_key="app_user.id", ondelete="CASCADE")
    session_id: uuid.UUID | None = Field(
        default=None, foreign_key="chat_session.id", ondelete="SET NULL"
    )
    """ The chat session driving this campaign, if any (so a resume can reattach the conversation). """
    created_at: str = Field(default="")
    updated_at: str = Field(default="")


CampaignStepStatus = Literal[
    "pending",  # created, not yet dispatched
    "dispatched",  # order issued, job(s) submitted
    "running",  # job(s) executing
    "completed",  # result collected
    "failed",  # the step's work failed
    "cancelled",  # cancelled by the planner/user
]


class CampaignStepBase(SQLModel):
    cycle: int = 0
    """ Zero-based optimization cycle this step belongs to. """
    kind: str
    """ The subagent role (e.g. "neutronics", "chemistry") or "decision" for a planner decision. """
    candidate: A[
        dict[str, Any] | None,
        Field(default=None, sa_column=Column(JSON, nullable=True)),
    ]
    """ The candidate composition/spec this step concerns; None for `decision` steps. """
    order_spec: A[dict[str, Any], Field(default_factory=dict, sa_column=Column(JSON))]
    """ The order given to the subagent (or the decision context). """
    status: A[
        CampaignStepStatus,
        Field(
            default="pending",
            sa_column=Column(String, nullable=False, default="pending"),
        ),
    ]
    result: A[
        dict[str, Any] | None,
        Field(default=None, sa_column=Column(JSON, nullable=True)),
    ]
    """ The structured, parsed result (collected outputs / scores); None until completed. """


class CampaignStepPublic(CampaignStepBase):
    id: uuid.UUID
    run_id: uuid.UUID
    created_at: str
    updated_at: str


class CampaignStepTable(CampaignStepBase, table=True):
    __tablename__: str = "campaign_step"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    run_id: uuid.UUID = Field(foreign_key="campaign_run.id", ondelete="CASCADE")
    created_at: str = Field(default="")
    updated_at: str = Field(default="")


class HpcJobBase(SQLModel):
    cluster: str
    """ "odo", "frontier", or "perlmutter". """
    job_name: str | None = None
    """ The `hpc_jobs/<name>` that was submitted. """
    state: str = Field(default="submitted")
    """ Last known job state (IRI/SLURM, e.g. PENDING/RUNNING/COMPLETED/FAILED); updated by the monitor. """
    log_path: str | None = None
    output_dir: str | None = None
    notified: bool = False
    """ Whether the completion email has been sent (so the monitor doesn't double-notify). """
    result_collected: bool = False
    """ Whether the subagent has parsed this job's outputs into its step result. """
    submitted_at: str = Field(default="")
    last_polled_at: str | None = None


class HpcJobPublic(HpcJobBase):
    job_id: str
    step_id: uuid.UUID
    user_id: uuid.UUID


class HpcJobTable(HpcJobBase, table=True):
    __tablename__: str = "hpc_job"

    job_id: str = Field(primary_key=True)
    """ The HPC job id returned by submit_hpc_job; unique across clusters (matches the MCP registry key). """
    step_id: uuid.UUID = Field(foreign_key="campaign_step.id", ondelete="CASCADE")
    user_id: uuid.UUID = Field(foreign_key="app_user.id", ondelete="CASCADE")


class CampaignCreate(BaseModel):
    """Fields a user supplies to start a campaign (the project comes from the URL)."""

    domain: str
    planner_skill: str
    title: str | None = None
    spec: dict[str, Any] = {}


class CampaignUpdate(BaseModel):
    """Fields editable on a campaign run via PATCH (omitted fields are left unchanged)."""

    title: str | None = None
    spec: dict[str, Any] | None = None
    plan: list[dict[str, Any]] | None = None
    status: CampaignStatus | None = None


class CampaignStatePublic(BaseModel):
    """Full campaign state for the UI / resume: the run plus its steps and jobs."""

    run: CampaignRunPublic
    steps: list[CampaignStepPublic]
    jobs: list[HpcJobPublic]


# ---------------------------------------------------------------------------
# Agent forum (multi-agent debate)
#
# A debate is one forum thread on a human-given topic, argued by role agents
# (proposer / reviewer / referee) until a round budget is spent or the human
# closes it. See docs/forum-git-format.md and openspec/changes/agent-forum/.
#
# The forum's git repository is the source of truth for what was said. These
# tables are a *projection* of it, rebuildable by reading the thread, and exist
# for the things git cannot cheaply do: list a user's debates, scope them to a
# project, and feed a change stream to the UI.
#
#   DebateRun         — the thread: topic, round budget, status, verdict.
#   DebateParticipant — a role on the debate: its identity and granted tools.
#   DebatePost        — one post, keeping what we know apart from what it claims.
# ---------------------------------------------------------------------------

DebateStatus = Literal[
    "setting_up",  # thread created, participants being attached
    "debating",  # rounds in flight
    "converged",  # round budget spent, referee posted a verdict
    "closed",  # the human, or a peer, ended it early with a CLOSED post
    "failed",  # the run could not continue
]


class DebateRunBase(SQLModel):
    topic: str
    """ The human's question, and the thread's title. """
    framing: str | None = None
    """ Any extra context the human gave; becomes the thread's first (TASK) post. """
    thread_id: str
    """ The forum thread's id. The join key back to the forum, which owns the real record. """
    rounds: int = 5
    """ Round budget. Each round is one proposal and the reviewer's answer to it. """
    rounds_done: int = 0
    status: A[
        DebateStatus,
        Field(
            default="setting_up",
            sa_column=Column(String, nullable=False, default="setting_up"),
        ),
    ]
    verdict: A[
        dict[str, Any] | None,
        Field(default=None, sa_column=Column(JSON, nullable=True)),
    ]
    """
    The referee's ranked hypothesis: claim, mechanism, falsifiable predictions,
    confidence, open risks. None until the debate reaches a verdict — a debate
    the human closes early may never have one, which is a real outcome and not
    a failure.
    """

    activity: str | None = None
    """
    What the debate is doing right now, in words, or None when nothing is.

    Exists because a turn is not instantaneous and produces nothing until it is
    finished. A role thinking for thirty seconds and a debate that has crashed
    look identical from outside, and now that a role can wait on a cluster job
    the gap between "working" and "frozen" is minutes rather than seconds.

    A sentence rather than a role name and a code, because what a reader wants
    is what is happening — "Reviewer is waiting for job 57719697 on perlmutter"
    is the whole answer, and reassembling that in the interface from parts would
    put the words furthest from the code that knows them.
    """

    activity_since: str | None = None
    """When the current activity started, so the interface can show how long."""

    thread_missing: bool | None = None
    """
    True once the forum no longer has this debate's thread — deleted on the
    forge, or written by h5i before the forum moved to plain git. The stored
    posts stay readable; posting and continuing are refused, and nothing keeps
    retrying the read.
    """


class DebateRunPublic(DebateRunBase):
    id: uuid.UUID
    project_id: uuid.UUID
    user_id: uuid.UUID
    created_at: str
    updated_at: str


class DebateRunTable(DebateRunBase, table=True):
    __tablename__: str = "debate_run"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    project_id: uuid.UUID = Field(foreign_key="project.id", ondelete="CASCADE")
    user_id: uuid.UUID = Field(foreign_key="app_user.id", ondelete="CASCADE")
    created_at: str = Field(default="")
    updated_at: str = Field(default="")


class DebateParticipantBase(SQLModel):
    identity: str
    """ The forum identity, e.g. `vista-proposer-1a2b3c4d`. Written on each post. """
    debate_role: str
    """ The scientific role: proposer, reviewer, referee. """
    forum_role: str
    """ The role name written on the role's posts. Today the same as `debate_role`. """
    active: bool = True
    """ Kept for older rows. A debate has one roster for its whole life, so always true. """
    granted_tools: A[list[str], Field(default_factory=list, sa_column=Column(JSON))]
    """
    The tools this role was allowed to use.

    Recorded because "this post used no tools" and "this role had no tools" look
    identical on a thread and mean very different things — the second is a
    configuration problem, and without this there is no way to see it.
    """


class DebateParticipantPublic(DebateParticipantBase):
    id: uuid.UUID
    run_id: uuid.UUID


class DebateParticipantTable(DebateParticipantBase, table=True):
    __tablename__: str = "debate_participant"
    __table_args__ = (UniqueConstraint("run_id", "identity"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    run_id: uuid.UUID = Field(foreign_key="debate_run.id", ondelete="CASCADE")


class DebatePostBase(SQLModel):
    post_id: str
    """ The post's uuid7. Unique across every install and stable across reads. """
    kind: str
    """ PROPOSAL, RISK, FINDING, ASK, DONE, TASK, CLOSED, UPVOTE, ... """
    body: str
    """
    The only agent-authored field on this row. Anything rendering a post has to
    keep that boundary visible, which is why it is not mixed into one blob with
    the rest.
    """
    sender: str
    """ The post's identity, or `human`. On a peer's post, their claim. """
    forum_role: str
    """ The post's role name. On a peer's post, their claim. """
    origin: str | None = None
    """ The host id of the install that wrote it. On a peer's post, their claim. """
    reply_to: str | None = None
    ts: str
    """ The post's own timestamp, not the time we projected it. Display only. """
    vouch_lane: str | None = None
    """
    `host-observed`, `peer-claimed` or `unattributed`.

    A separate column on purpose: what this install knows (it wrote the post)
    and what a post says about itself are different things, and folding the
    lane into the post row would erase that. A reader is entitled to know
    which they have.
    """
    denied: str | None = None
    """ A recorded refusal. Nothing in the git format writes one; kept for older rows. """

    published: bool | None = None
    """
    For a post this install wrote: whether the forum remote has it yet.

    Posting is local-first — a post is real once it is committed locally, and
    reaches the remote on the next successful sync. False is "not yet
    published", which the UI says rather than implying peers can see it. None
    on a peer's post, which by definition came from the remote.
    """

    on_remote: bool | None = None
    """
    False when a post we once read from the remote is no longer there — a peer
    rewrote the thread's history. The post stays here, because it was said;
    this marks that the forum no longer shows it. None until known.
    """

    authored_by: str | None = None
    """
    The VISTA account that wrote this, for posts this deployment made itself.

    Every operator's post is `sender="human"` with no name — the forum format
    has nowhere to put one, and a name arriving over the wire would be a claim
    rather than a fact. So this is filled in only where we genuinely know: the
    request that created the post was authenticated. It is stamped at post time
    rather than joined at read time — the record says who posted it then, not
    who owns that account now.

    Always None on a peer's post. Nothing we could put there would be knowledge.
    """
    votes: int = 0
    """ Net votes from posts this install wrote, one per voter, latest winning. """

    peer_votes: int = 0
    """
    Net votes that arrived over the remote.

    Kept apart from `votes` because the two answer different questions — what
    this forum's own participants would act on, versus what outside readers
    think. Summed, neither is legible.
    """
    round_index: int | None = None
    """ Which debate round produced this; None for the human's posts and peers'. """
    tools_used: A[
        list[dict[str, Any]], Field(default_factory=list, sa_column=Column(JSON))
    ]
    """
    The tools the agent called while producing this post: `{tool, detail}` each.

    This is what separates a grounded claim from an asserted one. An empty list
    on an agent's post is meaningful — it says the claim rests on the model
    alone.
    """


class DebatePostPublic(DebatePostBase):
    id: uuid.UUID
    run_id: uuid.UUID


class DebatePostTable(DebatePostBase, table=True):
    __tablename__: str = "debate_post"
    __table_args__ = (UniqueConstraint("run_id", "post_id"),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    run_id: uuid.UUID = Field(foreign_key="debate_run.id", ondelete="CASCADE")


class DebateCreate(BaseModel):
    """Fields a user supplies to open a debate (the project comes from the URL)."""

    topic: str
    framing: str | None = None
    rounds: int | None = None


class DebateStatePublic(BaseModel):
    """Full debate state for the UI: the run, who is on it, and what was said."""

    run: DebateRunPublic
    participants: list[DebateParticipantPublic]
    posts: list[DebatePostPublic]
    simulations: list[dict[str, Any]] = []
    """
    Every job this debate commissioned, and what became of it.

    A `commission_simulation` entry in a post's provenance proves only that a job
    was submitted. Whether it ran, failed, or is still queued lives on the
    campaign side — so without this a reader has a job id and no way to find out
    what happened to it, which is the state the record was in.
    """
