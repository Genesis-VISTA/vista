"""
One Slack thread is one VISTA conversation.

A top-level DM or mention starts a conversation (marked `origin="slack"`, so
the desktop shows where it came from) in the project the researcher chose for
Slack, or their only one; with several and none chosen, VISTA asks which
(`ChooseProject`, held in `slack_project_ask` until they tap one). Replies in
that thread continue it. Every message goes into the
conversation's inbox (`services/inbox.py`), so one that arrives while the
thread's turn is running waits for it rather than being dropped.
"""

import uuid
from dataclasses import dataclass

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import (
    ChatSessionCreate,
    ChatSessionTable,
    ProjectTable,
    SlackProjectAskTable,
    SlackThreadTable,
)
from ..services import chat_session as chat_session_service
from ..services import project as project_service
from .events import Message
from .store import now_iso

TITLE_LENGTH = 60


class NoProject(LookupError):
    """The researcher has no project for Slack conversations to run in."""


class ChooseProject(LookupError):
    """Several projects and none chosen for Slack: the researcher picks one."""

    def __init__(self, projects: list[ProjectTable]) -> None:
        super().__init__("Which project should this go in?")
        self.choices = sorted(
            ((p.id, p.name) for p in projects), key=lambda c: c[1].lower()
        )
        """ (id, name) of each, by name: read here, as the rows do not outlive the session. """


@dataclass(frozen=True)
class Conversation:
    chat_session_id: uuid.UUID
    project: ProjectTable
    is_new: bool


async def project_for(
    session: AsyncSession, project_id: uuid.UUID | None
) -> ProjectTable:
    """
    The chosen project, if it still exists, else the researcher's only one.

    Raises:
        NoProject: they have none.
        ChooseProject: they have several and none of them is chosen.
    """
    projects = await project_service.list_projects(session)
    if not projects:
        raise NoProject(
            "You don't have a VISTA project yet. Create one in the desktop app first."
        )
    chosen = next((p for p in projects if p.id == project_id), None)
    if chosen is not None:
        return chosen
    if len(projects) == 1:
        return projects[0]
    raise ChooseProject(projects)


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
    project_id: uuid.UUID | None,
) -> Conversation:
    """
    The conversation for `message`'s thread, started if it has none.

    `project_id` is the project to start one in: the link's, or the one the
    researcher picked for this thread.

    Raises:
        NotYourThread: someone else started this thread.
        NoProject: the researcher has no project to start one in.
        ChooseProject: VISTA must ask which project first.
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
        if chat is not None:
            project = await session.get(ProjectTable, chat.project_id)
            if project is not None:
                return Conversation(chat.id, project, is_new=False)
        # The conversation was deleted in the desktop app: start it again.
        await session.delete(row)
        await session.flush()

    project = await project_for(session, project_id)
    chat = await chat_session_service.create_chat_session(
        session,
        project_id=project.id,
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


async def held_ask(
    session: AsyncSession, team_id: str, channel_id: str, thread_ts: str
) -> SlackProjectAskTable | None:
    """The thread's question about which project, while nobody has answered it."""
    return (
        await session.exec(
            select(SlackProjectAskTable).where(
                SlackProjectAskTable.team_id == team_id,
                SlackProjectAskTable.channel_id == channel_id,
                SlackProjectAskTable.thread_ts == thread_ts,
            )
        )
    ).first()
