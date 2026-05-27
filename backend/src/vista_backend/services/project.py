from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import ProjectCreate, ProjectTable
from .project_agent import invalidate_agents


async def list_projects(session: AsyncSession) -> list[ProjectTable]:
    return list((await session.exec(select(ProjectTable))).all())


async def get_project_by_name_optional(session: AsyncSession, name: str) -> ProjectTable | None:
    return (await session.exec(select(ProjectTable).where(ProjectTable.name == name))).first()


async def get_project_by_name(session: AsyncSession, name: str) -> ProjectTable:
    project = await get_project_by_name_optional(session, name)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def create_project(session: AsyncSession, payload: ProjectCreate) -> ProjectTable:
    new = ProjectTable.model_validate(payload)
    session.add(new)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"A project named {payload.name!r} already exists.")
    await session.refresh(new)
    return new


async def update_project(session: AsyncSession, name: str, updates: ProjectCreate) -> ProjectTable:
    project = await get_project_by_name(session, name)
    for key, value in updates.model_dump().items():
        setattr(project, key, value)
    session.add(project)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"A project named {updates.name!r} already exists.")
    await session.refresh(project)
    invalidate_agents(session, project_id=project.id)
    return project


async def delete_project(session: AsyncSession, name: str) -> None:
    project = await get_project_by_name(session, name)
    project_id = project.id
    await session.delete(project)
    invalidate_agents(session, project_id=project_id)
