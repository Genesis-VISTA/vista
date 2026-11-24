"""Minimal `Project` / settings builders for agent tests."""

import uuid

from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.db.schemas import (
    LOCAL_USER_ID,
    LocalSettingsPublic,
    ProjectCreate,
    ProjectPublic,
    UserTable,
)
from vista_backend.services import project as project_service


def make_project(
    *,
    name: str | None = None,
    system_prompt: str | None = None,
    skills: list[str] | None = None,
    knowledge_bases: list[str] | None = None,
    tools: list[str] | None = None,
    usage_limits: dict | None = None,
) -> ProjectPublic:
    """An in-memory `ProjectPublic`, for tests that never touch the DB."""
    return ProjectPublic(
        id=uuid.uuid4(),
        name=name or f"proj-{uuid.uuid4().hex[:8]}",
        system_prompt=system_prompt,
        skills=skills or [],
        knowledge_bases=knowledge_bases or [],
        tools=tools if tools is not None else ["*"],
        usage_limits=usage_limits or {},
    )


def make_local_settings(
    *,
    email: str | None = None,
    odo_s3m_token: str | None = None,
    frontier_s3m_token: str | None = None,
    nersc_iri_token: str | None = None,
    odo_remote_dir: str | None = None,
) -> LocalSettingsPublic:
    """An in-memory `LocalSettingsPublic`, for tests that never touch the DB."""
    return LocalSettingsPublic(
        install_id=uuid.uuid4(),
        email=email,
        odo_s3m_token=odo_s3m_token,
        frontier_s3m_token=frontier_s3m_token,
        nersc_iri_token=nersc_iri_token,
        odo_remote_dir=odo_remote_dir,
    )


async def seed_project(
    session: AsyncSession,
    *,
    name: str | None = None,
    system_prompt: str | None = None,
    skills: list[str] | None = None,
    knowledge_bases: list[str] | None = None,
    tools: list[str] | None = None,
    usage_limits: dict | None = None,
    user: UserTable | None = None,
) -> ProjectPublic:
    """Insert a project row, owned by `user` (default: `local`), and return its public view."""
    owner = user or await session.get(UserTable, LOCAL_USER_ID)
    assert owner is not None
    row = await project_service.create_project(
        session,
        ProjectCreate(
            name=name or f"proj-{uuid.uuid4().hex[:8]}",
            system_prompt=system_prompt,
            skills=skills or [],
            knowledge_bases=knowledge_bases or [],
            tools=tools if tools is not None else ["*"],
            usage_limits=usage_limits or {},
        ),
        owner,
    )
    return ProjectPublic.model_validate(row)
