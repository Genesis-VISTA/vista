"""
One Slack thread is one VISTA conversation.

A top-level DM or mention starts a conversation (marked `origin="slack"`, so
the desktop shows where it came from) in the linked researcher's chosen
project; replies in that thread continue it. A message that arrives while the
thread's turn is still running waits for it, a few deep, rather than being
dropped.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import (
    ChatSessionCreate,
    ChatSessionTable,
    ProjectTable,
    SlackThreadTable,
    UserPublicWithConfig,
)
from ..services import chat_session as chat_session_service
from ..services import project as project_service
from .events import Message
from .store import now_iso

MAX_WAITING = 3
TITLE_LENGTH = 60


class NoProject(LookupError):
    """The researcher has no project for Slack conversations to run in."""


@dataclass(frozen=True)
class Conversation:
    chat_session_id: uuid.UUID
    project: ProjectTable
    is_new: bool


async def project_for(
    session: AsyncSession, user: UserPublicWithConfig, project_id: uuid.UUID | None
) -> ProjectTable:
    """The link's chosen project, if it still has access, else their first."""
    projects = await project_service.list_projects(session, user)
    if not projects:
        raise NoProject(
            "You don't have a VISTA project yet. Create one in the desktop app first."
        )
    chosen = next((p for p in projects if p.id == project_id), None)
    return chosen or sorted(projects, key=lambda p: p.name)[0]


def _title(text: str) -> str:
    line = " ".join(text.split())
    if len(line) <= TITLE_LENGTH:
        return line or "Slack conversation"
    return line[: TITLE_LENGTH - 1].rsplit(" ", 1)[0] + "…"


class NotYourThread(PermissionError):
    """The thread is another Slack user's conversation."""


async def conversation_for(
    session: AsyncSession,
    message: Message,
    *,
    user: UserPublicWithConfig,
    project_id: uuid.UUID | None,
) -> Conversation:
    """
    The conversation for `message`'s thread, started if it has none.

    Raises:
        NotYourThread: someone else started this thread.
        NoProject: the researcher has no project to start one in.
    """
    row = (
        await session.exec(
            select(SlackThreadTable).where(
                SlackThreadTable.team_id == message.team_id,
                SlackThreadTable.channel_id == message.channel_id,
                SlackThreadTable.thread_ts == message.root_ts,
            )
        )
    ).first()
    if row is not None:
        if row.slack_user_id != message.user_id:
            raise NotYourThread(
                "This thread is someone else's conversation with VISTA. "
                "Mention me in a new message to start your own."
            )
        chat = await session.get(ChatSessionTable, row.chat_session_id)
        if chat is not None and chat.user_id == user.id:
            project = await session.get(ProjectTable, chat.project_id)
            if project is not None:
                return Conversation(chat.id, project, is_new=False)
        # The conversation was deleted in the desktop app: start it again.
        await session.delete(row)
        await session.flush()

    project = await project_for(session, user, project_id)
    chat = await chat_session_service.create_chat_session(
        session,
        project_id=project.id,
        user_id=user.id,
        payload=ChatSessionCreate(title=_title(message.prompt)),
        origin="slack",
    )
    chat_id = chat.id
    session.add(
        SlackThreadTable(
            team_id=message.team_id,
            channel_id=message.channel_id,
            thread_ts=message.root_ts,
            chat_session_id=chat_id,
            slack_user_id=message.user_id,
            created_at=now_iso(),
        )
    )
    await session.commit()
    await session.refresh(project)
    return Conversation(chat_id, project, is_new=True)


async def thread_of(
    session: AsyncSession, chat_session_id: uuid.UUID
) -> SlackThreadTable | None:
    return (
        await session.exec(
            select(SlackThreadTable).where(
                SlackThreadTable.chat_session_id == chat_session_id
            )
        )
    ).first()


class ThreadQueue:
    """
    Turns in one conversation run one after another.

    `run(key, job)` runs `job` once every earlier job for `key` has finished.
    At most `MAX_WAITING` may wait behind a running one; past that `run`
    returns False at once and the caller says so.
    """

    def __init__(self) -> None:
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}
        self._waiting: dict[uuid.UUID, int] = {}

    def busy(self, key: uuid.UUID) -> bool:
        lock = self._locks.get(key)
        return lock is not None and lock.locked()

    async def run(self, key: uuid.UUID, job: Callable[[], Awaitable[None]]) -> bool:
        lock = self._locks.setdefault(key, asyncio.Lock())
        if lock.locked() and self._waiting.get(key, 0) >= MAX_WAITING:
            return False
        self._waiting[key] = self._waiting.get(key, 0) + 1
        try:
            async with lock:
                self._waiting[key] -= 1
                await job()
        finally:
            if self._waiting.get(key) == 0 and not lock.locked():
                self._locks.pop(key, None)
                self._waiting.pop(key, None)
        return True
