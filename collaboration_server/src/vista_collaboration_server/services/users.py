"""
Who has signed in: the people a team's members can add to it.

A signed-in request records its caller (`identity.get_user`), so that someone
can be found by name or email once they have used the server. Writing on every
request would turn each read into a write, so a caller is written only when
what the token says of them has changed, or once an hour to keep `last_seen`
roughly right.
"""

import time
from typing import TYPE_CHECKING

from fastapi import FastAPI
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import UserTable, now_iso

if TYPE_CHECKING:  # `identity` imports this module to record its callers
    from .identity import User

_REFRESH_SECONDS = 3600


async def record(app: FastAPI, user: "User") -> None:
    """Write `user` to the user table, unless this process wrote the same lately."""
    seen: dict[str, tuple[str | None, str | None, float]] = app.state.users_seen
    known = seen.get(user.sub)
    now = time.monotonic()
    if (
        known is not None
        and known[:2] == (user.email, user.name)
        and now - known[2] < _REFRESH_SECONDS
    ):
        return
    async with AsyncSession(app.state.engine) as session:
        for attempt in range(2):
            row = await session.get(UserTable, user.sub)
            if row is None:
                row = UserTable(sub=user.sub)
            row.email = user.email
            row.name = user.name
            row.last_seen = now_iso()
            session.add(row)
            try:
                await session.commit()
                break
            except IntegrityError:
                # Their first two requests at once: the other inserted them.
                await session.rollback()
                if attempt:
                    raise
    seen[user.sub] = (user.email, user.name, now)


def _like(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def search(session: AsyncSession, q: str | None, limit: int) -> list[UserTable]:
    """Everyone who has signed in whose name or email contains `q`, by name."""
    query = select(UserTable)
    if q and q.strip():
        pattern = _like(q.strip())
        query = query.where(
            or_(
                col(UserTable.name).ilike(pattern, escape="\\"),
                col(UserTable.email).ilike(pattern, escape="\\"),
            )
        )
    query = query.order_by(
        col(UserTable.name).is_(None), col(UserTable.name), col(UserTable.email)
    ).limit(limit)
    return list((await session.exec(query)).all())
