import uuid
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.agents import ProjectAgent
from ..db.db import get_engine
from ..db.schemas import ProjectPublic, ProjectTable, UserPublicWithConfig, UserTable
from .ttl_pool import TTLPool


ProjectAgentKey = tuple[uuid.UUID, uuid.UUID]
""" (project_id, user_id) """

# TODO: Should probably create a "services" folder and move this and other logic into it.

async def _build_project_agent(project_id: uuid.UUID, user_id: uuid.UUID) -> ProjectAgent:
    async with AsyncSession(get_engine()) as session:
        project_row = (await session.exec(
            select(ProjectTable).where(ProjectTable.id == project_id)
        )).first()
        user_row = (await session.exec(
            select(UserTable).where(UserTable.id == user_id)
        )).first()
        if project_row is None or user_row is None:
            raise RuntimeError(f"Project {project_id} or user {user_id} not found")
        project = ProjectPublic.model_validate(project_row)
        user = UserPublicWithConfig.model_validate(user_row)
    agent = ProjectAgent(project, user)
    await agent.__aenter__()
    return agent


async def _cleanup_project_agent(agent: ProjectAgent) -> None:
    # Drop any pending elicitations owned by this agent so /projects/{project_name}/elicitation
    # callers don't find a stale reference once the MCP session is torn down.
    for eid in [eid for eid, a in _active_elicitations.items() if a is agent]:
        _active_elicitations.pop(eid, None)
    agent.cancel_elicitations()
    await agent.__aexit__(None, None, None)

# TODO: These process-local state. Multi-worker deployments would need to push this the db somehow
project_agent_pool: TTLPool[ProjectAgentKey, ProjectAgent] = TTLPool(
    ttl=timedelta(minutes=30),
    max_size=50,
    factory=lambda key: _build_project_agent(*key),
    cleanup=_cleanup_project_agent,
)
_active_elicitations: dict[str, ProjectAgent] = {}


def register_elicitation(elicitation_id: str, agent: ProjectAgent) -> None:
    """ Track which agent owns a pending elicitation so resolutions can be routed back. """
    _active_elicitations[elicitation_id] = agent


def resolve_elicitation(
    elicitation_id: str,
    action: Literal["accept", "decline", "cancel"],
    content: dict[str, Any] | None = None,
) -> bool:
    """ Resolve a pending elicitation. Returns False if no such elicitation is registered. """
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
    user_id: uuid.UUID | None = None,
) -> None:
    """
    Register an agent pool eviction to run after the session commits.

    - project_id only: evict all agents for that project
    - user_id only: evict all agents for that user
    - both: evict only the specific (project, user) pair
    - neither: raises ValueError
    """
    if project_id is None and user_id is None:
        raise ValueError("At least one of project_id or user_id must be provided")
    session.info.setdefault("_pending_agent_invalidations", []).append((project_id, user_id))


@event.listens_for(Session, "after_commit")
def _flush_agent_invalidations(session: Session) -> None:
    pending: list[tuple] = session.info.pop("_pending_agent_invalidations", [])
    for project_id, user_id in pending:
        for key in list(project_agent_pool.keys()):
            if project_id is not None and user_id is not None:
                match = key == (project_id, user_id)
            elif project_id is not None:
                match = key[0] == project_id
            else:
                match = key[1] == user_id
            if match:
                project_agent_pool.delete(key)
