"""
Items and their versions, and the teams they belong to: the server's whole data
model.

An item is anything a researcher publishes (a skill, a project, a knowledge
base), told apart only by `kind`. It is created together with its first
version, so every item has one; each later publish adds the next, numbered
2, 3… by the server. A version holds a manifest (JSON) and optionally one blob,
and is never changed. The server reads neither.

Every item belongs to one team, and only the team's members can see it or
publish to it. Everyone is a member of the public team, which has no member
rows: anything published for everyone goes there. It starts empty.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated as A, Any

from pydantic import StringConstraints, field_validator
from sqlalchemy import JSON, Column, Index, UniqueConstraint, text
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


PUBLIC_TEAM_ID = uuid.UUID("00000000-0000-4000-8000-000000000000")
""" The public team's id, the same on every server. """

PUBLIC_TEAM_NAME = "public"

SERVER_CREATOR = "vista"
""" The `created_by` of what the server makes itself (the public team), not anyone who signs in. """


def _strip(v):
    return v.strip() if isinstance(v, str) else v


class TeamTable(CollabTable, table=True):
    __tablename__: str = "team"
    # Names are unique however they are capitalised: "Salts" and "salts" would
    # be the same team to anyone choosing between them.
    __table_args__ = (Index("ix_team_name_lower", text("lower(name)"), unique=True),)
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str
    created_by: str
    created_by_name: str | None = None
    created_at: str = Field(default_factory=now_iso)


class TeamMemberTable(CollabTable, table=True):
    """One member of a team. The public team has none: everyone is in it."""

    __tablename__: str = "team_member"
    team_id: uuid.UUID = Field(
        foreign_key="team.id", primary_key=True, ondelete="CASCADE"
    )
    sub: str = Field(foreign_key="user.sub", primary_key=True, index=True)
    added_by: str
    added_at: str = Field(default_factory=now_iso)


class UserTable(CollabTable, table=True):
    """
    Everyone who has signed in, as they last did: who can be added to a team.
    Written by `services/users.py` on a signed-in request.
    """

    __tablename__: str = "user"
    sub: str = Field(primary_key=True)
    email: str | None = None
    name: str | None = None
    last_seen: str = Field(default_factory=now_iso)


class BlobUploadTable(CollabTable, table=True):
    """
    Who uploaded a blob. A blob is named by its SHA-256, so knowing the name is
    no proof of having the bytes: a version may name only a blob its publisher
    uploaded, or one in a version they can see (`services/blob_access.py`).
    """

    __tablename__: str = "blob_upload"
    blob_ref: str = Field(primary_key=True)
    sub: str = Field(primary_key=True)
    uploaded_at: str = Field(default_factory=now_iso)


class ItemTable(CollabTable, table=True):
    __tablename__: str = "item"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    kind: str = Field(index=True)
    name: str
    team_id: uuid.UUID = Field(foreign_key="team.id", index=True)
    """ The team whose members can see the item and publish to it. """
    copied_from: uuid.UUID | None = None
    """ The item this one was copied from (`POST /items/{id}/copy`), if any. """
    copied_from_version: int | None = None
    """ The version of `copied_from` its version 1 is. """
    created_by: str
    """ Who created the item: their Keycloak `sub` (in dev-headers mode, an install id). """
    created_by_name: str | None = None
    """ Their name at the time, for showing; never used to decide anything. """
    created_by_email: str | None = None
    """ Kept here, not in any answer: who may see it is for access control to decide. """
    created_by_install: uuid.UUID | None = None
    """ The VISTA installation they created it from, as its request claimed. """
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
    created_by: str
    created_by_name: str | None = None
    created_by_email: str | None = None
    created_by_install: uuid.UUID | None = None
    created_at: str = Field(default_factory=now_iso)


class ForumTable(CollabTable, table=True):
    """
    A hosted forum: a bare git repository under `data_dir/forums/<id>.git`
    (`services/forums.py`). Owned by whoever made it, and theirs alone until it
    is linked to a `project` item, once; from then on it belongs to that item's
    team. The link never changes.
    """

    __tablename__: str = "forum"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_sub: str = Field(index=True)
    item_id: uuid.UUID | None = Field(default=None, foreign_key="item.id", unique=True)
    created_at: str = Field(default_factory=now_iso)


class ForumPushTable(CollabTable, table=True):
    """
    One post (or a thread's root) the server accepted, and who pushed it:
    written by the `post-receive` hook (`services/forum_hook.py`). `seq` orders
    the records for `GET /forums/{id}/pushes?after=`. A thread's root is
    recorded as kind `THREAD`, with the thread's id as `post_id`: it is how a
    later `CLOSED` finds the opener.
    """

    __tablename__: str = "forum_push"
    __table_args__ = (UniqueConstraint("forum_id", "thread_id", "post_id"),)
    seq: int | None = Field(default=None, primary_key=True)
    forum_id: uuid.UUID = Field(foreign_key="forum.id", index=True, ondelete="CASCADE")
    thread_id: str
    post_id: str
    kind: str
    commit: str
    pusher_sub: str
    pusher_name: str | None = None
    imported: bool = False
    """ Copied onto the forum by an import push: the pusher did not write it. """
    received_at: str = Field(default_factory=now_iso)


class ForumPublic(SQLModel):
    id: uuid.UUID
    git_url: str
    """ Where a git client fetches and pushes it: `<server>/git/<id>.git`. """
    item_id: uuid.UUID | None
    created_at: str


class ForumLink(SQLModel):
    item_id: uuid.UUID
    """ A `project` item the caller can see. """


class ForumPushPublic(SQLModel):
    seq: int
    thread_id: str
    post_id: str
    kind: str
    commit: str
    pusher_sub: str
    pusher_name: str | None
    imported: bool
    received_at: str


class ItemCreate(SQLModel):
    """A new item and its version 1, created together. The server chooses its id."""

    kind: Kind
    name: str = Field(min_length=1, max_length=200)
    team_id: uuid.UUID
    """
    The team it is published to, one the caller is in: `PUBLIC_TEAM_ID` for
    everyone. Required, so that nothing is made public by leaving it out.
    """
    manifest: dict[str, Any]
    blob_ref: BlobRef | None = None
    """ A blob uploaded with `POST /blobs`, or none. """

    _strip_name = field_validator("name", mode="before")(_strip)


class ItemPublic(SQLModel):
    id: uuid.UUID
    kind: str
    name: str
    team_id: uuid.UUID
    copied_from: uuid.UUID | None
    copied_from_version: int | None
    created_by: str
    created_by_name: str | None
    created_by_install: uuid.UUID | None
    head_version: int
    created_at: str


class ItemCopy(SQLModel):
    """A copy of an item's head version into another team, as a new item."""

    team_id: uuid.UUID
    """ A team the caller is in. """
    name: str | None = Field(default=None, min_length=1, max_length=200)
    """ The copy's name; the original's by default. """

    _strip_name = field_validator("name", mode="before")(_strip)


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
    created_by: str
    created_by_name: str | None
    created_by_install: uuid.UUID | None
    created_at: str


class BlobPublic(SQLModel):
    blob_ref: str
    size: int


class UserPublic(SQLModel):
    """Someone who has signed in: `GET /users`, and a team's members."""

    sub: str
    email: str | None
    name: str | None


class TeamCreate(SQLModel):
    name: str = Field(min_length=1, max_length=100)

    _strip_name = field_validator("name", mode="before")(_strip)


class TeamPublic(SQLModel):
    id: uuid.UUID
    name: str
    public: bool
    """ The public team, which everyone is in and which has no members to manage. """
    created_by: str
    created_by_name: str | None
    created_at: str


class MemberCreate(SQLModel):
    sub: str = Field(min_length=1)
    """ Someone who has signed in to this server (`GET /users`). """


class MemberPublic(UserPublic):
    added_by: str
    added_at: str


class MePublic(UserPublic):
    """Who the server takes the caller to be, and their teams: `GET /me`."""

    teams: list[TeamPublic]


class ServerVersion(SQLModel):
    server_version: str
    """ This server's release, for a person reading it. """
    api_version: int
    """ The HTTP API it speaks; see `vista_collaboration_server.API_VERSION`. """
