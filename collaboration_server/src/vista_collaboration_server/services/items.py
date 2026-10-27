"""
Creating items and publishing their versions.

An item is created with its version 1, in one transaction, so there is never an
item with nothing in it. Every later publish is a compare-and-set on the item's
head: a version is accepted only when it names the current head as its parent,
so two researchers who both publish on top of version 3 cannot both become
version 4. The second is told the head instead, and decides for themselves what
to do about it.
"""

import uuid

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import (
    ItemCreate,
    ItemTable,
    ItemVersionTable,
    VersionCreate,
)
from .identity import User


class NotFound(Exception):
    """No such item, or no such version of one; the message says which."""


class ItemExists(Exception):
    """A create named an id that an item has already."""


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
    """The item and its version 1, committed together."""
    item = ItemTable(
        id=data.id or uuid.uuid4(),
        kind=data.kind,
        name=data.name,
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
    try:
        await session.commit()
    except IntegrityError as e:
        # Only the id can collide: the version's key is the item's.
        await session.rollback()
        raise ItemExists(f"An item with the id {item.id} exists already.") from e
    await session.refresh(item)
    return item


async def get_item(session: AsyncSession, item_id: uuid.UUID) -> ItemTable:
    item = await session.get(ItemTable, item_id)
    if item is None:
        raise NotFound("No such item.")
    return item


async def list_items(session: AsyncSession, kind: str | None) -> list[ItemTable]:
    query = select(ItemTable).order_by(col(ItemTable.created_at))
    if kind is not None:
        query = query.where(ItemTable.kind == kind)
    return list((await session.exec(query)).all())


async def list_versions(
    session: AsyncSession, item_id: uuid.UUID
) -> list[ItemVersionTable]:
    await get_item(session, item_id)
    query = (
        select(ItemVersionTable)
        .where(ItemVersionTable.item_id == item_id)
        .order_by(col(ItemVersionTable.version))
    )
    return list((await session.exec(query)).all())


async def get_version(
    session: AsyncSession, item_id: uuid.UUID, version: int
) -> ItemVersionTable:
    row = await session.get(ItemVersionTable, (item_id, version))
    if row is None:
        await get_item(session, item_id)  # an unknown item says so
        raise NotFound("No such version.")
    return row


async def publish(
    session: AsyncSession,
    item_id: uuid.UUID,
    data: VersionCreate,
    user: User,
) -> ItemVersionTable:
    """
    Add the next version, if `data.parent_version` is still the head.

    One transaction, opened by the conditional `UPDATE` itself: on SQLite that
    statement takes the write lock, so a concurrent publish waits for this one
    to commit and then finds the head moved. Nothing is read before it, which
    would have started a read snapshot the update could not then write from.
    """
    parent = data.parent_version
    result = await session.exec(
        update(ItemTable)
        .where(col(ItemTable.id) == item_id, col(ItemTable.head_version) == parent)
        .values(head_version=parent + 1)
    )
    if result.rowcount != 1:
        await session.rollback()
        item = await get_item(session, item_id)
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
