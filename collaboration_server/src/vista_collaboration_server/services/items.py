"""
Creating items and publishing their versions.

An item is created with its version 1, in one transaction, so there is never an
item with nothing in it. Every later publish is a compare-and-set on the item's
head: a version is accepted only when it names the current head as its parent,
so two researchers who both publish on top of version 3 cannot both become
version 4. The second is told the head instead, and decides for themselves what
to do about it.

Every read and write here is for a user, and an item in a team they are not in
is `NotFound` to them (`teams.visible_teams`). An item can be copied into
another of the caller's teams, as a new item sharing the original's blob.
"""

import uuid

from sqlalchemy import update
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import (
    ItemCopy,
    ItemCreate,
    ItemTable,
    ItemVersionTable,
    VersionCreate,
)
from . import forums
from .errors import NotFound
from .identity import User
from .teams import get_team, visible_teams

__all__ = ["NotFound", "VersionConflict"]


class VersionConflict(Exception):
    """The publish named a parent that is no longer the item's head."""

    def __init__(self, head_version: int, parent_version: int):
        self.head_version = head_version
        self.parent_version = parent_version
        super().__init__(
            f"This item's head is version {head_version}, but the publish was "
            f"built on version {parent_version}."
        )


def _publisher(user: User) -> dict:
    return {
        "created_by": user.sub,
        "created_by_name": user.name,
        "created_by_email": user.email,
        "created_by_install": user.install_id,
    }


async def create_item(session: AsyncSession, data: ItemCreate, user: User) -> ItemTable:
    """The item and its version 1 in `data.team_id`, a team `user` is in, committed together."""
    await get_team(session, data.team_id, user)
    item = ItemTable(
        kind=data.kind,
        name=data.name,
        team_id=data.team_id,
        **_publisher(user),
        head_version=1,
    )
    session.add(item)
    session.add(
        ItemVersionTable(
            item_id=item.id,
            version=1,
            parent_version=None,
            manifest=data.manifest,
            blob_ref=data.blob_ref,
            **_publisher(user),
        )
    )
    await session.commit()
    await session.refresh(item)
    return item


async def get_item(session: AsyncSession, item_id: uuid.UUID, user: User) -> ItemTable:
    """The item, if it is in a team `user` is in; `NotFound` otherwise, whether or not it exists."""
    query = select(ItemTable).where(
        ItemTable.id == item_id, visible_teams(col(ItemTable.team_id), user)
    )
    item = (await session.exec(query)).first()
    if item is None:
        raise NotFound("No such item.")
    return item


async def list_items(
    session: AsyncSession,
    user: User,
    kind: str | None = None,
    team_id: uuid.UUID | None = None,
) -> list[ItemTable]:
    """The items `user` can see, oldest first; only `kind`'s, or `team_id`'s, if given."""
    query = (
        select(ItemTable)
        .where(visible_teams(col(ItemTable.team_id), user))
        .order_by(col(ItemTable.created_at))
    )
    if kind is not None:
        query = query.where(ItemTable.kind == kind)
    if team_id is not None:
        query = query.where(ItemTable.team_id == team_id)
    return list((await session.exec(query)).all())


async def list_versions(
    session: AsyncSession, item_id: uuid.UUID, user: User
) -> list[ItemVersionTable]:
    await get_item(session, item_id, user)
    query = (
        select(ItemVersionTable)
        .where(ItemVersionTable.item_id == item_id)
        .order_by(col(ItemVersionTable.version))
    )
    return list((await session.exec(query)).all())


async def get_version(
    session: AsyncSession, item_id: uuid.UUID, version: int, user: User
) -> ItemVersionTable:
    await get_item(session, item_id, user)
    row = await session.get(ItemVersionTable, (item_id, version))
    if row is None:
        raise NotFound("No such version.")
    return row


async def publish(
    session: AsyncSession,
    item_id: uuid.UUID,
    data: VersionCreate,
    user: User,
) -> ItemVersionTable:
    """
    Add the next version, if `data.parent_version` is still the head and the
    item is in a team `user` is in.

    One transaction, opened by the conditional `UPDATE` itself: on SQLite that
    statement takes the write lock, so a concurrent publish waits for this one
    to commit and then finds the head moved. Nothing is read before it, which
    would have started a read snapshot the update could not then write from;
    so the team is a condition of the update too, and only once it has missed
    is the item read, to say why.
    """
    parent = data.parent_version
    result = await session.exec(
        update(ItemTable)
        .where(
            col(ItemTable.id) == item_id,
            col(ItemTable.head_version) == parent,
            visible_teams(col(ItemTable.team_id), user),
        )
        .values(head_version=parent + 1)
    )
    if result.rowcount != 1:
        await session.rollback()
        item = await get_item(session, item_id, user)
        raise VersionConflict(item.head_version, parent)
    row = ItemVersionTable(
        item_id=item_id,
        version=parent + 1,
        parent_version=parent,
        manifest=data.manifest,
        blob_ref=data.blob_ref,
        **_publisher(user),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def copy_item(
    session: AsyncSession, item_id: uuid.UUID, data: ItemCopy, user: User
) -> ItemTable:
    """
    A new item in `data.team_id` whose version 1 is the head of `item_id`: the
    same manifest, and the same blob, so nothing is copied but the row. `user`
    must see the original and be in the team. The original is left as it is.
    """
    source = await get_item(session, item_id, user)
    head = await get_version(session, item_id, source.head_version, user)
    await get_team(session, data.team_id, user)
    item = ItemTable(
        kind=source.kind,
        name=data.name or source.name,
        team_id=data.team_id,
        copied_from=source.id,
        copied_from_version=head.version,
        **_publisher(user),
        head_version=1,
    )
    session.add(item)
    # The item before its version: nothing orders the inserts by their key.
    await session.flush()
    # A forum is the original's own: the copy gets a new, empty one.
    await forums.copy_forum_row(session, source.id, item.id, user)
    session.add(
        ItemVersionTable(
            item_id=item.id,
            version=1,
            parent_version=None,
            manifest=head.manifest,
            blob_ref=head.blob_ref,
            **_publisher(user),
        )
    )
    await session.commit()
    await session.refresh(item)
    return item
