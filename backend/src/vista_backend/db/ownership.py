"""
Who owns a row: the data layer's one answer.

Every query for an owned table (project, chat session, campaign run, debate run,
HPC job, background task, skill, knowledge base) goes through `owned` or
`get_owned`, so a route cannot forget the filter. Another user's row is
indistinguishable from one that does not exist: `get_owned` returns `None` and
the route answers `404`.

Skills and knowledge bases also have rows that belong to nobody (`user_id` NULL):
the skills VISTA bundles and the OLCF docs corpus. `visible` and `get_visible`
add those, for reading and for resolving names; they are writable by none, which
the write paths enforce (`skills.update_skill`, `delete_skill`, ...).

The user is always passed explicitly, from a route's `CurrentUser` down.
Child rows (messages, files, posts, steps, inbox items) have no `user_id`: they
are reached through their owned parent.
"""

import uuid
from typing import Any, TypeVar

from sqlalchemy import event, inspect as sa_inspect, or_
from sqlalchemy.orm import Session
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from .schemas import UserTable

T = TypeVar("T")


def uid(user: UserTable) -> str:
    """
    `user`'s id, read without touching the database: a commit expires the row a
    route was handed, and reading an expired attribute does IO, which an async
    session cannot do implicitly.
    """
    state = sa_inspect(user, raiseerr=False)
    identity = state.identity if state is not None else None
    return identity[0] if identity else user.id


def owned(stmt: SelectOfScalar[T] | Any, user: UserTable, table: Any = None) -> Any:
    """
    `stmt` restricted to rows `user` owns. `table` is the model whose `user_id`
    to test; by default the statement's first entity.
    """
    table = table if table is not None else _first_entity(stmt)
    return stmt.where(col(table.user_id) == uid(user))


def visible(stmt: Any, user: UserTable, table: Any = None) -> Any:
    """`stmt` restricted to rows `user` owns plus the global ones (`user_id` NULL)."""
    table = table if table is not None else _first_entity(stmt)
    return stmt.where(
        or_(col(table.user_id) == uid(user), col(table.user_id).is_(None))
    )


def _first_entity(stmt: Any) -> Any:
    return stmt.column_descriptions[0]["entity"]


async def get_owned(
    session: AsyncSession, table: type[T], row_id: uuid.UUID | str, user: UserTable
) -> T | None:
    """The row of `table` with primary key `row_id`, if `user` owns it; else `None`."""
    row = await session.get(table, row_id)
    if row is None or getattr(row, "user_id", None) != uid(user):
        return None
    return row


async def get_visible(
    session: AsyncSession, table: type[T], row_id: uuid.UUID | str, user: UserTable
) -> T | None:
    """Like `get_owned`, but a global row (`user_id` NULL) counts too."""
    row = await session.get(table, row_id)
    if row is None or getattr(row, "user_id", None) not in (uid(user), None):
        return None
    return row


async def inherited_owner(session: AsyncSession, table: Any, row_id: Any) -> str | None:
    """
    The `user_id` of the owned row `row_id` of `table` (a project, a chat session,
    a campaign run), for a row made on its behalf: a campaign run, debate run,
    HPC job or background task belongs to whoever owns what it was started in.
    """
    parent = await session.get(table, row_id)
    return getattr(parent, "user_id", None)


REQUIRED_OWNER_TABLES = frozenset(
    {
        "project",
        "chat_session",
        "background_task",
        "campaign_run",
        "hpc_job",
        "debate_run",
        "slack_link",
    }
)
""" Owned tables whose rows always have an owner (skills and knowledge bases may be nobody's). """


class OwnerMissing(RuntimeError):
    """A hosted insert into an owned table named no owner: a bug, never guessed at."""


@event.listens_for(Session, "before_flush")
def require_owner(session: Session, _context: Any, _instances: Any) -> None:
    """
    Every new row of `REQUIRED_OWNER_TABLES` has its `user_id`. On a desktop a
    missing one is `local`'s, its only user (what the column's server default
    says too); a hosted deployment refuses the insert, so the default can never
    quietly hand a user's row to `local`.
    """
    from ..config import settings
    from .schemas import LOCAL_USER_ID

    for obj in session.new:
        table = getattr(obj, "__tablename__", None)
        if table not in REQUIRED_OWNER_TABLES or obj.user_id is not None:
            continue
        if settings.deployment == "hosted":
            raise OwnerMissing(
                f"A new {table} row has no owner; on a hosted deployment every "
                "one must name the user it belongs to."
            )
        obj.user_id = LOCAL_USER_ID
