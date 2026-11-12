"""
A conversation's inbox (openspec change background-tasks, design Decision 1).

Everything addressed to a conversation's agent enters here: the researcher's
messages and the results of its background tasks. Nothing is pushed into a
running turn. The turn pulls: `InboxDelivery`, a capability passed to each
chat run, takes what is queued between the agent's steps. So an item is
editable exactly until a turn has taken it, and an item put after a turn's last
step is still queued when the turn ends, for the registry to start the next
turn with.

`take_queued`, `edit` and `withdraw` are each one conditional UPDATE, so a take
and an edit racing on the same item cannot both succeed.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal

from pydantic_ai import ModelRequestNode, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import UserPromptPart
from pydantic_graph import End
from sqlalchemy import update
from sqlalchemy.exc import OperationalError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import ChatSessionTable, InboxItemTable
from ..utils.misc import now_iso

log = logging.getLogger(__name__)

ItemKind = Literal["message", "task_event"]

AUTO_TURN_CAP = 10
""" Turns VISTA starts in a row without a message from the researcher (Decision 3). """


class ItemNotFound(Exception):
    """No such inbox item in this conversation."""


class ItemTaken(Exception):
    """The item is no longer queued: a turn took it, or it was withdrawn."""

    def __init__(self, item: InboxItemTable) -> None:
        super().__init__(f"Inbox item {item.id} is {item.state}")
        self.item = item


PutListener = Callable[[uuid.UUID], Awaitable[None]]
_listeners: list[PutListener] = []


def on_put(listener: PutListener) -> Callable[[], None]:
    """
    Call `listener(chat_session_id)` after each `put` commits. Returns a function
    that removes it. The chat run registry listens, to start a turn in an idle
    conversation.
    """
    _listeners.append(listener)
    return lambda: _listeners.remove(listener)


async def put(
    db: AsyncSession,
    chat_session_id: uuid.UUID,
    *,
    kind: ItemKind,
    text: str,
    payload: dict[str, Any] | None = None,
    wakes: bool = True,
    notify: bool = True,
) -> InboxItemTable:
    """
    Queue an item, commit, then tell the listeners. Returns a detached copy, as all of these do.

    `notify=False` leaves the listeners out, for a caller that starts the turn
    itself and must see why it could not (the send endpoint).
    """
    item = InboxItemTable(
        chat_session_id=chat_session_id,
        kind=kind,
        text=text,
        payload=payload,
        wakes=wakes,
        created_at=now_iso(),
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    item = _detached(item)
    for listener in list(_listeners) if notify else []:
        try:
            await listener(chat_session_id)
        except Exception:
            log.exception("Inbox listener failed for %s", chat_session_id)
    return item


async def list_queued(
    db: AsyncSession, chat_session_id: uuid.UUID
) -> list[InboxItemTable]:
    """The conversation's queued items, in arrival order."""
    return list(
        (
            await db.exec(
                select(InboxItemTable)
                .where(
                    InboxItemTable.chat_session_id == chat_session_id,
                    InboxItemTable.state == "queued",
                )
                .order_by(col(InboxItemTable.id))
            )
        ).all()
    )


async def get(
    db: AsyncSession, chat_session_id: uuid.UUID, item_id: int
) -> InboxItemTable:
    item = await db.get(InboxItemTable, item_id, populate_existing=True)
    if item is None or item.chat_session_id != chat_session_id:
        raise ItemNotFound(str(item_id))
    return item


async def _set_if_queued(
    db: AsyncSession, chat_session_id: uuid.UUID, item_id: int, **values: Any
) -> InboxItemTable:
    result = await db.exec(
        update(InboxItemTable)
        .where(
            col(InboxItemTable.id) == item_id,
            col(InboxItemTable.chat_session_id) == chat_session_id,
            col(InboxItemTable.state) == "queued",
        )
        .values(**values)
    )
    await db.commit()
    item = _detached(await get(db, chat_session_id, item_id))
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise ItemTaken(item)
    return item


async def edit(
    db: AsyncSession, chat_session_id: uuid.UUID, item_id: int, text: str
) -> InboxItemTable:
    """Change a queued item's text. `ItemTaken` once a turn has it."""
    return await _set_if_queued(db, chat_session_id, item_id, text=text)


async def withdraw(
    db: AsyncSession, chat_session_id: uuid.UUID, item_id: int
) -> InboxItemTable:
    """Take a queued item back so no turn receives it. `ItemTaken` once a turn has it."""
    return await _set_if_queued(db, chat_session_id, item_id, state="withdrawn")


async def withdraw_messages(
    db: AsyncSession, chat_session_id: uuid.UUID
) -> list[InboxItemTable]:
    """Withdraw every queued researcher message, as Stop does. Task results stay queued."""
    rows = (
        await db.exec(
            update(InboxItemTable)
            .where(
                col(InboxItemTable.chat_session_id) == chat_session_id,
                col(InboxItemTable.state) == "queued",
                col(InboxItemTable.kind) == "message",
            )
            .values(state="withdrawn")
            .returning(InboxItemTable)
        )
    ).all()
    return await _detached_after_commit(db, rows)


def _id(item: InboxItemTable) -> int:
    assert item.id is not None
    return item.id


def _detached(item: InboxItemTable) -> InboxItemTable:
    """A copy outside the session, which a later commit cannot expire."""
    return InboxItemTable.model_validate(item)


async def _detached_after_commit(db: AsyncSession, rows) -> list[InboxItemTable]:
    """Copy `RETURNING` rows out of the session, then commit (which would expire them)."""
    items = sorted((_detached(r[0]) for r in rows), key=_id)
    await db.commit()
    return items


async def take_queued(
    db: AsyncSession, chat_session_id: uuid.UUID
) -> list[InboxItemTable]:
    """Mark every queued item taken and return them in arrival order."""
    rows = (
        await db.exec(
            update(InboxItemTable)
            .where(
                col(InboxItemTable.chat_session_id) == chat_session_id,
                col(InboxItemTable.state) == "queued",
            )
            .values(state="taken", taken_at=now_iso())
            .returning(InboxItemTable)
        )
    ).all()
    return await _detached_after_commit(db, rows)


async def requeue(db: AsyncSession, items: Sequence[InboxItemTable]) -> None:
    """Put taken items back, for a turn that could not start with them."""
    await db.exec(
        update(InboxItemTable)
        .where(col(InboxItemTable.id).in_([_id(i) for i in items]))
        .values(state="queued", taken_at=None)
    )
    await db.commit()


def may_start_turn(row: ChatSessionTable, queued: Sequence[InboxItemTable]) -> bool:
    """
    Whether these queued items may start a turn in an idle conversation (Decision 3).

    A researcher message always may. Otherwise an item that wakes may, unless
    Stop made the conversation quiet or VISTA has started `AUTO_TURN_CAP` turns
    in a row.
    """
    if any(i.kind == "message" for i in queued):
        return True
    return (
        any(i.wakes for i in queued)
        and not row.quiet
        and row.auto_turns < AUTO_TURN_CAP
    )


async def waiting_on_researcher(
    db: AsyncSession, chat_session_ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    """
    The conversations among these with a task result queued, which, while no
    turn runs, waits for the researcher: "needs you" (Decision 3).
    """
    if not chat_session_ids:
        return set()
    rows = (
        await db.exec(
            select(InboxItemTable.chat_session_id)
            .where(
                col(InboxItemTable.chat_session_id).in_(chat_session_ids),
                InboxItemTable.state == "queued",
                InboxItemTable.kind == "task_event",
            )
            .distinct()
        )
    ).all()
    return set(rows)


TakeItems = Callable[[], Awaitable[list[InboxItemTable]]]


class InboxDelivery(AbstractCapability[Any]):
    """
    Hands queued inbox items to a running turn between the agent's steps.

    PydanticAI drains `ctx.enqueue`d messages into the next model request from
    its own outermost `before_model_request` hook, which runs before ours would.
    So items are taken one hook earlier, before each model request node runs, and
    once more when the run would end, ahead of the drain's end-of-run redirect
    (its `after_node_run` runs last). Each taken item becomes its own user prompt
    part, in arrival order.

    `take` marks the items taken and reports them to the run's watchers. If the
    database is locked, which a campaign tool's unflushed transaction in the same
    turn can cause, this step delivers nothing and the items wait for the next.
    """

    def __init__(self, take: TakeItems) -> None:
        self._take = take

    async def _deliver(self, ctx: RunContext[Any]) -> bool:
        try:
            items = await self._take()
        except OperationalError:
            log.warning("Inbox delivery skipped a step: the database is locked")
            return False
        if items:
            ctx.enqueue(*(UserPromptPart(content=i.text) for i in items))
        return bool(items)

    async def before_node_run(self, ctx, *, node):
        if isinstance(node, ModelRequestNode):
            await self._deliver(ctx)
        return node

    async def after_node_run(self, ctx, *, node, result):
        if isinstance(result, End):
            await self._deliver(ctx)
        return result
