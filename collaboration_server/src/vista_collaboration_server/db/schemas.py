"""
Items and their versions: the server's whole data model.

An item is anything a researcher publishes (a skill, a project, a knowledge
base), told apart only by `kind`. It is created together with its first
version, so every item has one; each later publish adds the next, numbered
2, 3… by the server. A version holds a manifest (JSON) and optionally one blob,
and is never changed. The server reads neither.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated as A, Any

from pydantic import StringConstraints, field_validator
from sqlalchemy import JSON, Column
from sqlalchemy.orm import registry
from sqlmodel import Field, SQLModel


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


KIND_PATTERN = r"^[a-z][a-z0-9-]{0,31}$"
""" What an item's kind looks like: `skill`, `project`, `knowledge-base`. """

BLOB_REF_PATTERN = r"^[0-9a-f]{64}$"
""" A blob's reference: the SHA-256 of its bytes, in lowercase hex. """

Kind = A[str, StringConstraints(pattern=KIND_PATTERN)]
BlobRef = A[str, StringConstraints(pattern=BLOB_REF_PATTERN)]


class CollabTable(SQLModel, registry=registry()):
    """
    The base of this server's tables, on a registry (and so a `MetaData`) of its
    own rather than `SQLModel.metadata`.

    The backend's tests import this package into the same process as the
    backend's own models; on the shared metadata each `create_all` would make
    the other's tables too, and a backend table named `item` would collide.
    """


class ItemTable(CollabTable, table=True):
    __tablename__: str = "item"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    kind: str = Field(index=True)
    name: str
    created_by: uuid.UUID
    """ The install id of the VISTA that created the item. """
    created_by_name: str | None = None
    """ Its display name at the time, for showing; never used to decide anything. """
    head_version: int = 1
    """ The newest version's number. """
    created_at: str = Field(default_factory=now_iso)


class ItemVersionTable(CollabTable, table=True):
    """One published version. Never updated or deleted."""

    __tablename__: str = "item_version"
    item_id: uuid.UUID = Field(
        foreign_key="item.id", primary_key=True, ondelete="CASCADE"
    )
    version: int = Field(primary_key=True)
    parent_version: int | None = None
    """ The head this version was published on top of; `None` only for version 1. """
    manifest: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    blob_ref: str | None = None
    created_by: uuid.UUID
    created_by_name: str | None = None
    created_at: str = Field(default_factory=now_iso)


class ItemCreate(SQLModel):
    """A new item and its version 1, created together."""

    id: uuid.UUID | None = None
    """
    The item's id, when the client must know it beforehand: one every client
    derives alike, so that whichever creates the item first, the others find
    it. `409` if an item has it already. None: the server chooses.
    """
    kind: Kind
    name: str = Field(min_length=1, max_length=200)
    manifest: dict[str, Any]
    blob_ref: BlobRef | None = None
    """ A blob uploaded with `POST /blobs`, or none. """

    @field_validator("name", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v


class ItemPublic(SQLModel):
    id: uuid.UUID
    kind: str
    name: str
    created_by: uuid.UUID
    created_by_name: str | None
    head_version: int
    created_at: str


class VersionCreate(SQLModel):
    parent_version: int = Field(ge=1)
    """
    The head this version builds on, which must still be the head. Required, so
    a client cannot leave it out and publish over a version it has not seen.
    """
    manifest: dict[str, Any]
    blob_ref: BlobRef | None = None
    """ A blob uploaded with `POST /blobs`, or none. """


class VersionPublic(SQLModel):
    item_id: uuid.UUID
    version: int
    parent_version: int | None
    manifest: dict[str, Any]
    blob_ref: str | None
    created_by: uuid.UUID
    created_by_name: str | None
    created_at: str


class BlobPublic(SQLModel):
    blob_ref: str
    size: int


class ServerVersion(SQLModel):
    server_version: str
    """ This server's release, for a person reading it. """
    api_version: int
    """ The HTTP API it speaks; see `vista_collaboration_server.API_VERSION`. """
