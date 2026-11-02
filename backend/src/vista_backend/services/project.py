from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import ProjectCreate, ProjectTable
from .project_agent import invalidate_agents
from .teams import check_references, team_for_new


def project_content(project: ProjectTable) -> dict[str, Any]:
    """
    The project's own fields, the ones `ProjectCreate` sets, as JSON: what a
    write changes and `modified` tracks.
    """
    return ProjectCreate.model_validate(project, from_attributes=True).model_dump(
        mode="json"
    )


async def list_projects(session: AsyncSession) -> list[ProjectTable]:
    return list((await session.exec(select(ProjectTable))).all())


async def get_project_by_name_optional(
    session: AsyncSession, name: str
) -> ProjectTable | None:
    return (
        await session.exec(select(ProjectTable).where(ProjectTable.name == name))
    ).first()


async def get_project_by_name(session: AsyncSession, name: str) -> ProjectTable:
    project = await get_project_by_name_optional(session, name)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def create_project(session: AsyncSession, payload: ProjectCreate) -> ProjectTable:
    # `modified` by default: never published, so all of it is local.
    new = ProjectTable.model_validate(payload)
    new.team_id = await team_for_new(session)
    await check_references(
        session, payload.name, new.team_id, payload.skills, payload.knowledge_bases
    )
    session.add(new)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail=f"A project named {payload.name!r} already exists."
        )
    await session.refresh(new)
    return new


async def update_project(
    session: AsyncSession, name: str, updates: ProjectCreate
) -> ProjectTable:
    project = await get_project_by_name(session, name)
    # Only what it gains: what it used already was checked when it was added.
    await check_references(
        session,
        name,
        project.team_id,
        [s for s in updates.skills if s not in project.skills],
        [k for k in updates.knowledge_bases if k not in project.knowledge_bases],
    )
    before = project_content(project)
    for key, value in updates.model_dump().items():
        setattr(project, key, value)
    # Only a real change: the UI saves the whole project to load one skill.
    if project_content(project) != before:
        project.modified = True
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


async def delete_project(session: AsyncSession, name: str) -> None:
    project = await get_project_by_name(session, name)
    project_id = project.id
    await session.delete(project)
    invalidate_agents(session, project_id=project_id)
