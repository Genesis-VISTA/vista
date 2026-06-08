"""
Data models / schemas
"""
import re
import uuid
from typing import Annotated as A, Literal, Optional
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
    knowledge_bases: A[list[str], Field(default_factory=list, sa_column=Column(JSON))] # TODO should make this a foreign key later
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
    usage_limits: A[dict, Field(default_factory=dict, sa_column=Column(JSON))]
    """
    Limits on the agent such as tool call depth
    Stored as a dict so we can store it as JSON in the DB, SQLModel won't parse the JSON into a
    UsageModel type directly.
    See pydantic_ai.UsageLimits for allowed values.
    """

    @field_validator('usage_limits', mode='after')
    @classmethod
    def _validate_usage_limits(cls, value):
        ta = TypeAdapter(UsageLimits)
        return ta.dump_python(ta.validate_python(value), mode = 'json')

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
    """ Project fields the user can set """
    pass


class ProjectPublic(ProjectBase):
    """ Project fields the user can read """
    id: uuid.UUID


class ProjectTable(ProjectBase, table=True):
    """ Project SQL model """
    __tablename__ = "project"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)


class ProjectMemberTable(SQLModel, table=True):
    """
    Membership join table. A row grants `user_id` read+write access to
    `project_id`. The composite primary key makes a (project, user) pair
    unique, so a duplicate add raises IntegrityError.
    """
    __tablename__ = "project_member"
    project_id: uuid.UUID = Field(foreign_key="project.id", primary_key=True, ondelete="CASCADE")
    user_id: uuid.UUID = Field(foreign_key="app_user.id", primary_key=True, ondelete="CASCADE")




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
    "queued",     # registered for indexing, indexer hasn't started this row yet
    "indexing",   # indexer is actively working on this row
    "indexed",    # text chunks (and possibly citation row) are in chroma
    "failed",     # indexing was attempted and raised; see index_error
]


PublicationCitationStatus = Literal[
    "pending",    # citation extraction hasn't been attempted yet
    "extracted",  # LLM returned a parsed citation; metadata fields are populated
    "skipped",    # the PDF was already in chroma; citation reused from prior run
    "failed",     # LLM call ran but produced an error or unparseable output
    "disabled",   # no LLM credentials configured, citation extraction is off
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
    "ready",    # collections populated; ready to serve search
    "stale",    # new PDFs added since the last successful index
    "failed",   # last indexing attempt raised
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

    publications: A[list[Publication], Field(default_factory=list, sa_column=Column(JSON))]
    build_status: A[
        KnowledgeBaseBuildStatus,
        Field(default="pending", sa_column=Column(String, nullable=False, default="pending")),
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
    """ Fields editable on an existing KB. """
    name: str | None = None
    description: str | None = None


class KnowledgeBasePublic(KnowledgeBaseBase):
    id: uuid.UUID
    created_at: str
    updated_at: str


class KnowledgeBaseTable(KnowledgeBaseBase, table=True):
    """ Knowledge Base SQL model. """
    __tablename__ = "knowledge_base"
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


class UserBase(SQLModel):
    pass


# Treat "" the same as None for the optional config fields so a cleared frontend
# input doesn't end up as a non-null-but-empty token/account in the DB
_USER_CONFIG_NULLABLE_FIELDS = (
    "nersc_account", "nersc_remote_dir",
    "frontier_account", "frontier_remote_dir",
    "s3m_token", "nersc_iri_token", "globus_token",
)
def _empty_str_to_none(v): return None if v == "" else v


class UserCreate(UserBase):
    email: str
    is_admin: bool = False
    remote_hpc_jobs_dir: str = "/gpfs/wolf2/olcf/gen150/proj-shared/vista"
    nersc_account: str | None = None
    nersc_remote_dir: str | None = None
    frontier_account: str | None = None
    frontier_remote_dir: str | None = None
    s3m_token: str | None = None
    nersc_iri_token: str | None = None
    globus_token: str | None = None

    @field_validator(*_USER_CONFIG_NULLABLE_FIELDS, mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        return _empty_str_to_none(v)


class UserUpdate(UserBase):
    is_admin: bool | None = None
    remote_hpc_jobs_dir: str | None
    nersc_account: str | None = None
    nersc_remote_dir: str | None = None
    frontier_account: str | None = None
    frontier_remote_dir: str | None = None
    s3m_token: str | None = None
    nersc_iri_token: str | None = None
    globus_token: str | None = None

    @field_validator(*_USER_CONFIG_NULLABLE_FIELDS, mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        return _empty_str_to_none(v)


class UserSelfUpdate(UserBase):
    remote_hpc_jobs_dir: str | None = None
    nersc_account: str | None = None
    nersc_remote_dir: str | None = None
    frontier_account: str | None = None
    frontier_remote_dir: str | None = None
    s3m_token: str | None = None
    nersc_iri_token: str | None = None
    globus_token: str | None = None

    @field_validator(*_USER_CONFIG_NULLABLE_FIELDS, mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        return _empty_str_to_none(v)


class UserPublic(UserBase):
    id: uuid.UUID
    email: str
    is_admin: bool = False


class UserPublicWithConfig(UserBase):
    """ Full user view for the authenticated user — includes decrypted token fields. """
    id: uuid.UUID
    email: str
    is_admin: bool = False
    remote_hpc_jobs_dir: str = "/gpfs/wolf2/olcf/gen150/proj-shared/vista"
    nersc_account: str | None = None
    nersc_remote_dir: str | None = None
    frontier_account: str | None = None
    frontier_remote_dir: str | None = None
    s3m_token: str | None = None
    nersc_iri_token: str | None = None
    globus_token: str | None = None


class UserTable(SQLModel, table=True):
    __tablename__ = "app_user"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    email: str = Field(unique=True)
    is_admin: bool = False
    remote_hpc_jobs_dir: str = "/gpfs/wolf2/olcf/gen150/proj-shared/vista"
    """ Folder on the HPC cluster (Odo) where hpc_jobs will be copied. """
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
    s3m_token: str | None = Field(default=None, sa_column=Column(EncryptedStr, nullable=True))
    """ Bearer token for S3M API authentication. Encrypted at rest. """
    nersc_iri_token: str | None = Field(default=None, sa_column=Column(EncryptedStr, nullable=True))
    """
    Globus access token for NERSC IRI. Encrypted at rest. Expires ~48h.
    Refresh: python iri-api-get-globus-token-main/get_globus_token.py --refresh-only
    """
    globus_token: str | None = Field(default=None, sa_column=Column(EncryptedStr, nullable=True))
    """
    Globus Transfer refresh token, used for Frontier file ops via the OLCF
    DTN collection. Long-lived; the MCP server mints short-lived access
    tokens from it on each submission via `globus_sdk.RefreshTokenAuthorizer`.
    Encrypted at rest.
    Obtain with: python OLCF-Globus-Transfer/get_olcf_token.py --force-login
                       --session-domain sso.ccs.ornl.gov
    Then copy the "refresh_token" field from ~/.globus/olcf_tokens.json.
    """
