import uuid

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import (
    ProjectCreate,
    ProjectMemberTable,
    ProjectTable,
    UserTable,
)
from . import user as user_service
from .project_agent import invalidate_agents
from ._helpers import ServiceUser


async def _is_member(
    session: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    row = (
        await session.exec(
            select(ProjectMemberTable).where(
                ProjectMemberTable.project_id == project_id,
                ProjectMemberTable.user_id == user_id,
            )
        )
    ).first()
    return row is not None


async def _ensure_project_access(
    session: AsyncSession, project: ProjectTable, user: ServiceUser
) -> None:
    """Raise 403 unless `user` may access `project`. `user="system"` bypasses the check."""
    if user == "system" or user.is_admin:
        return
    if not await _is_member(session, project.id, user.id):
        raise HTTPException(
            status_code=403, detail="You do not have access to this project"
        )


async def list_projects(session: AsyncSession, user: ServiceUser) -> list[ProjectTable]:
    if user == "system" or user.is_admin:
        return list((await session.exec(select(ProjectTable))).all())
    stmt = (
        select(ProjectTable)
        .join(
            ProjectMemberTable,
            col(ProjectMemberTable.project_id) == col(ProjectTable.id),
        )
        .where(ProjectMemberTable.user_id == user.id)
    )
    return list((await session.exec(stmt)).all())


async def get_project_by_name_optional(
    session: AsyncSession, name: str, user: ServiceUser
) -> ProjectTable | None:
    project = (
        await session.exec(select(ProjectTable).where(ProjectTable.name == name))
    ).first()
    if project:
        await _ensure_project_access(session, project, user)
    return project


async def get_project_by_name(
    session: AsyncSession, name: str, user: ServiceUser
) -> ProjectTable:
    project = await get_project_by_name_optional(session, name, user)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def create_project(
    session: AsyncSession, payload: ProjectCreate, user: ServiceUser
) -> ProjectTable:
    new = ProjectTable.model_validate(payload)
    session.add(new)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail=f"A project named {payload.name!r} already exists."
        )
    if user != "system":
        session.add(ProjectMemberTable(project_id=new.id, user_id=user.id))
        await session.flush()
    await session.refresh(new)
    return new


async def update_project(
    session: AsyncSession, name: str, updates: ProjectCreate, user: ServiceUser
) -> ProjectTable:
    project = await get_project_by_name(session, name, user)
    for key, value in updates.model_dump().items():
        setattr(project, key, value)
    session.add(project)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail=f"A project named {updates.name!r} already exists."
        )
    await session.refresh(project)
    invalidate_agents(session, project_id=project.id)
    return project


async def delete_project(session: AsyncSession, name: str, user: ServiceUser) -> None:
    project = await get_project_by_name(session, name, user)
    project_id = project.id
    await session.delete(project)
    invalidate_agents(session, project_id=project_id)


async def list_project_members(
    session: AsyncSession, name: str, user: ServiceUser
) -> list[UserTable]:
    project = await get_project_by_name(session, name, user)
    stmt = (
        select(UserTable)
        .join(ProjectMemberTable, col(ProjectMemberTable.user_id) == col(UserTable.id))
        .where(ProjectMemberTable.project_id == project.id)
    )
    return list((await session.exec(stmt)).all())


async def add_project_member(
    session: AsyncSession, name: str, target_user_email: str, user: ServiceUser
) -> None:
    project = await get_project_by_name(session, name, user)
    target = await user_service.get_user_by_email(
        session, target_user_email, user="system"
    )
    session.add(ProjectMemberTable(project_id=project.id, user_id=target.id))
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="User is already a member of this project"
        )
