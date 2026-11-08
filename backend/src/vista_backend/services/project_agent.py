import uuid
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.agents import ProjectAgent
from ..db.db import get_engine
from ..db.schemas import ChatSessionTable, ProjectPublic, ProjectTable
from ..utils.ttl_pool import TTLPool
from . import chat_session as chat_session_service, local_settings


ProjectAgentKey = tuple[uuid.UUID | None, uuid.UUID]
""" (chat_session_id | None, project_id) """


async def get_project_agent_key(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    chat_session_id: uuid.UUID | None = None,
) -> ProjectAgentKey:
    if chat_session_id is None:
        return (None, project_id)
    chat_session = await chat_session_service.get_or_create_chat_session(
        session,
        project_id=project_id,
        chat_session_id=chat_session_id,
    )
    return (chat_session.id, project_id)


async def find_live_project_agent_key(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    chat_session_id: uuid.UUID | None = None,
) -> ProjectAgentKey | None:
    if chat_session_id is None:
        key = (None, project_id)
        return key if key in project_agent_pool.keys() else None
    chat_session = await chat_session_service.get_chat_session_optional(
        session,
        project_id=project_id,
        chat_session_id=chat_session_id,
    )
    if chat_session is None:
        return None
    key = (chat_session.id, project_id)
    return key if key in project_agent_pool.keys() else None


async def _build_project_agent(
    session_id: uuid.UUID | None, project_id: uuid.UUID
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
        local = await local_settings.get_public(session)
        model_override = None
        if session_id is not None:
            chat_row = await session.get(ChatSessionTable, session_id)
            model_override = chat_row.inference_model if chat_row else None
    agent = ProjectAgent(project, local, session_id, model_override)
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
project_agent_pool: TTLPool[ProjectAgentKey, ProjectAgent] = TTLPool(
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
) -> None:
    """
    Register an agent pool eviction to run after the session commits.

    Each given id narrows the match; an agent is evicted only when it matches
    all of them:

    - project_id: every agent for that project
    - chat_session_id: that one chat's agent
    - neither: every agent at all (a settings change, which every agent was
      built from)
    """
    session.info.setdefault("_pending_agent_invalidations", []).append(
        (project_id, chat_session_id)
    )


@event.listens_for(Session, "after_commit")
def _flush_agent_invalidations(session: Session) -> None:
    pending: list[tuple[uuid.UUID | None, uuid.UUID | None]] = session.info.pop(
        "_pending_agent_invalidations", []
    )
    for project_id, chat_session_id in pending:
        for key in list(project_agent_pool.keys()):
            # key is (session_id, project_id)
            if (chat_session_id is None or key[0] == chat_session_id) and (
                project_id is None or key[1] == project_id
            ):
                project_agent_pool.delete(key)
