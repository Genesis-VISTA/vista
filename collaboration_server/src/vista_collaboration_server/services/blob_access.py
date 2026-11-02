"""
Which blobs a user may name in a version they publish.

Blobs are stored by their SHA-256 and served only through a version the caller
can see. Without this check, anyone who learned a blob's reference (a former
member of a team, say) could publish an item of their own naming it, and read
it through that. So a version may name a blob only if its publisher uploaded
it themselves, or can see a version naming it already (a copy, or a version
built on one they were given). The refusal is the same as for a blob never
uploaded, so it says nothing about what the server holds.
"""

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import BlobUploadTable, ItemTable, ItemVersionTable
from .identity import User
from .teams import visible_teams


async def record_upload(session: AsyncSession, blob_ref: str, user: User) -> None:
    """Record that `user` uploaded `blob_ref` (again, it may be)."""
    if await session.get(BlobUploadTable, (blob_ref, user.sub)) is not None:
        return
    session.add(BlobUploadTable(blob_ref=blob_ref, sub=user.sub))
    try:
        await session.commit()
    except IntegrityError:
        # The same upload twice at once: recorded either way.
        await session.rollback()


async def may_use(session: AsyncSession, blob_ref: str, user: User) -> bool:
    """Whether `user` may name `blob_ref` in a version."""
    if await session.get(BlobUploadTable, (blob_ref, user.sub)) is not None:
        return True
    seen = (
        select(ItemVersionTable.item_id)
        .join(ItemTable, col(ItemTable.id) == col(ItemVersionTable.item_id))
        .where(
            ItemVersionTable.blob_ref == blob_ref,
            visible_teams(col(ItemTable.team_id), user),
        )
        .limit(1)
    )
    return (await session.exec(seen)).first() is not None
