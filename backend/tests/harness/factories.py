"""Minimal `Project` / `User` builders for agent tests."""

import uuid

from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.db.schemas import (
    ProjectCreate,
    ProjectPublic,
    UserPublicWithConfig,
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


def make_user(
    *,
    email: str | None = None,
    is_admin: bool = False,
    odo_s3m_token: str | None = None,
    frontier_s3m_token: str | None = None,
    nersc_iri_token: str | None = None,
    frontier_account: str | None = None,
) -> UserPublicWithConfig:
    """An in-memory `UserPublicWithConfig`, for tests that never touch the DB."""
    return UserPublicWithConfig(
        id=uuid.uuid4(),
        email=email or f"{uuid.uuid4().hex[:8]}@ornl.gov",
        is_admin=is_admin,
        odo_s3m_token=odo_s3m_token,
        frontier_s3m_token=frontier_s3m_token,
        nersc_iri_token=nersc_iri_token,
        frontier_account=frontier_account,
    )


async def seed_user(
    session: AsyncSession, *, email: str | None = None, is_admin: bool = False
) -> UserPublicWithConfig:
    """Insert a user row and return its public view."""
    row = UserTable(
        id=uuid.uuid4(),
        email=email or f"{uuid.uuid4().hex[:8]}@ornl.gov",
        is_admin=is_admin,
    )
    session.add(row)
    await session.flush()
    return UserPublicWithConfig.model_validate(row)


async def seed_project(
    session: AsyncSession,
    owner: UserPublicWithConfig,
    *,
    name: str | None = None,
    system_prompt: str | None = None,
    skills: list[str] | None = None,
    knowledge_bases: list[str] | None = None,
    tools: list[str] | None = None,
    usage_limits: dict | None = None,
) -> ProjectPublic:
    """
    Insert a project row owned by `owner` (who becomes its only member) and
    return its public view.
    """
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
