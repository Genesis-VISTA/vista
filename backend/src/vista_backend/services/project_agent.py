import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.agents import ProjectAgent
from ..db.db import get_engine
from ..db.schemas import (
    LOCAL_USER_ID,
    ChatSessionTable,
    ProjectPublic,
    ProjectTable,
    UserTable,
)
from ..config import settings
from ..utils.ttl_pool import TTLPool
from . import chat_session as chat_session_service, local_settings


ProjectAgentKey = tuple[str, uuid.UUID | None, uuid.UUID]
""" (user_id, chat_session_id | None, project_id) """


async def get_project_agent_key(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    chat_session_id: uuid.UUID | None = None,
    user: UserTable,
) -> ProjectAgentKey:
    if chat_session_id is None:
        return (user.id, None, project_id)
    chat_session = await chat_session_service.get_or_create_chat_session(
        session,
        project_id=project_id,
        chat_session_id=chat_session_id,
        user=user,
    )
    return (user.id, chat_session.id, project_id)


async def find_live_project_agent_key(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    chat_session_id: uuid.UUID | None = None,
    user: UserTable,
) -> ProjectAgentKey | None:
    if chat_session_id is None:
        key = (user.id, None, project_id)
        return key if key in project_agent_pool.keys() else None
    chat_session = await chat_session_service.get_chat_session_optional(
        session,
        project_id=project_id,
        chat_session_id=chat_session_id,
        user=user,
    )
    if chat_session is None:
        return None
    key = (user.id, chat_session.id, project_id)
    return key if key in project_agent_pool.keys() else None


async def _build_project_agent(
    user_id: str, session_id: uuid.UUID | None, project_id: uuid.UUID
) -> ProjectAgent:
    async with AsyncSession(get_engine()) as session:
        project_row = (
            await session.exec(
                select(ProjectTable).where(ProjectTable.id == project_id)
            )
        ).first()
        if project_row is None:
            raise RuntimeError(f"Project {project_id} not found")
        project = ProjectPublic.model_validate(project_row)
        local = await local_settings.get_public(session, user_id)
        model_override = None
        if session_id is not None:
            chat_row = await session.get(ChatSessionTable, session_id)
            model_override = chat_row.inference_model if chat_row else None
    agent = ProjectAgent(
        project,
        local,
        session_id,
        model_override,
        owner_id=project_row.user_id or LOCAL_USER_ID,
    )
    await agent.__aenter__()
    return agent


async def _cleanup_project_agent(agent: ProjectAgent) -> None:
    # Drop any pending elicitations owned by this agent so /projects/{project_name}/elicitation
    # callers don't find a stale reference once the MCP session is torn down.
    for eid in [eid for eid, a in _active_elicitations.items() if a is agent]:
        _active_elicitations.pop(eid, None)
    agent.cancel_elicitations()
    await agent.__aexit__(None, None, None)


# TODO: These are process-local state. Multi-worker deployments would need to push this to the db somehow
class SandboxCapReached(HTTPException):
    """No sandbox can be started: a cap is full and none of the candidates is idle. `429`, its detail the reason the UI shows."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=429, detail=detail)


class AgentPool(TTLPool[ProjectAgentKey, ProjectAgent]):
    """
    The agent pool with the sandbox caps (`settings.sandboxes_per_user`, then
    `sandboxes_total`). A new key at a cap closes the least recently used IDLE
    entry (the requesting user's own, never another's); an entry something holds
    (a running chat turn, a background command) is never closed. None idle:
    `SandboxCapReached`, with the reason.
    """

    @asynccontextmanager
    async def get(self, key: ProjectAgentKey) -> AsyncIterator[ProjectAgent]:
        if key not in self._items:
            self._admit(key[0])
        async with super().get(key) as agent:
            yield agent

    def _admit(self, user_id: str) -> None:
        self.expire()
        for limit, mine, what in (
            (settings.sandboxes_per_user, True, "You have"),
            (settings.sandboxes_total, False, "This server has"),
        ):
            while self.count(user_id if mine else None) >= limit:
                if not self.evict_lru_idle(user_id):
                    raise SandboxCapReached(
                        f"{what} {limit} sandboxes open, all busy. Wait for a "
                        "running task or turn to finish, or stop one, then try again."
                    )

    def count(self, user_id: str | None) -> int:
        return sum(1 for k in self._items if user_id is None or k[0] == user_id)

    def evict_lru_idle(self, user_id: str) -> bool:
        """Close `user_id`'s least recently used idle entry. False when none is idle."""
        for key, entry in self._items.items():  # oldest first
            if key[0] == user_id and entry.ref_count == 0:
                self._delete(key)
                return True
        return False


project_agent_pool: AgentPool = AgentPool(
    ttl=timedelta(minutes=30),
    max_size=50,
    factory=lambda key: _build_project_agent(*key),
    cleanup=_cleanup_project_agent,
)
_active_elicitations: dict[str, ProjectAgent] = {}


def register_elicitation(elicitation_id: str, agent: ProjectAgent) -> None:
    """Track which agent owns a pending elicitation so resolutions can be routed back."""
    _active_elicitations[elicitation_id] = agent


def resolve_elicitation(
    elicitation_id: str,
    action: Literal["accept", "decline", "cancel"],
    content: dict[str, Any] | None = None,
) -> bool:
    """Resolve a pending elicitation. Returns False if no such elicitation is registered."""
    agent = _active_elicitations.pop(elicitation_id, None)
    if agent is None:
        return False
    try:
        agent.resolve_elicitation(elicitation_id, action, content)
    except KeyError:
        return False
    return True


def invalidate_agents(
    session: AsyncSession,
    *,
    project_id: uuid.UUID | None = None,
    chat_session_id: uuid.UUID | None = None,
    user_id: str | None = None,
) -> None:
    """
    Register an agent pool eviction to run after the session commits.

    Each given id narrows the match; an agent is evicted only when it matches
    all of them:

    - project_id: every agent for that project
    - chat_session_id: that one chat's agent
    - user_id: only that user's agents (a settings change: pass the saving
      user's id). Without it, every user's.
    - none of them: every agent at all
    """
    session.info.setdefault("_pending_agent_invalidations", []).append(
        (project_id, chat_session_id, user_id)
    )


@event.listens_for(Session, "after_commit")
def _flush_agent_invalidations(session: Session) -> None:
    pending: list[tuple[uuid.UUID | None, uuid.UUID | None, str | None]] = (
        session.info.pop("_pending_agent_invalidations", [])
    )
    for project_id, chat_session_id, user_id in pending:
        for key in list(project_agent_pool.keys()):
            # key is (user_id, session_id, project_id)
            if (
                (user_id is None or key[0] == user_id)
                and (chat_session_id is None or key[1] == chat_session_id)
                and (project_id is None or key[2] == project_id)
            ):
                project_agent_pool.delete(key)
