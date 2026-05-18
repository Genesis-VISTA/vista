"""
Data models / schemas
"""
import uuid
from typing import Annotated as A
from sqlalchemy import JSON, Column
from pydantic_ai import UsageLimits
from pydantic import TypeAdapter, field_validator, StringConstraints
from sqlmodel import Field, SQLModel


class ProjectBase(SQLModel):
    name: A[str, StringConstraints(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9_ .\-]+$"), Field(unique=True)]
    description: str | None = None
    system_prompt: str | None = None
    skills: A[list[str], Field(default_factory=list, sa_column=Column(JSON))]
    """ List of skills available to this project """
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
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
