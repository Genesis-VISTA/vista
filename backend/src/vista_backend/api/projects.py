from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ..db.db import SessionDep
from ..db.schemas import ProjectCreate, ProjectPublic, ProjectTable
from ..utils.project import invalidate_agents


router = APIRouter(prefix="/projects", tags=["projects"])

@router.get("")
async def list_projects(session: SessionDep) -> list[ProjectPublic]:
    projects = (await session.exec(select(ProjectTable))).all()
    return [ProjectPublic.model_validate(p) for p in projects]


@router.get("/{project_name}")
async def get_project(project_name: str, session: SessionDep) -> ProjectPublic:
    project = (await session.exec(select(ProjectTable).where(ProjectTable.name == project_name))).first()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectPublic.model_validate(project)


@router.post("", status_code=201)
async def create_project(payload: ProjectCreate, session: SessionDep) -> ProjectPublic:
    new = ProjectTable.model_validate(payload)
    session.add(new)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"A project named {payload.name!r} already exists.")
    await session.refresh(new)
    return ProjectPublic.model_validate(new)


@router.put("/{project_name}")
async def update_project(project_name: str, updates: ProjectCreate, session: SessionDep) -> ProjectPublic:
    project = (await session.exec(select(ProjectTable).where(ProjectTable.name == project_name))).first()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
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
    return ProjectPublic.model_validate(project)


@router.delete("/{project_name}", status_code=204)
async def delete_project(project_name: str, session: SessionDep) -> None:
    existing = (await session.exec(select(ProjectTable).where(ProjectTable.name == project_name))).first()
    if existing is None:
        raise HTTPException(status_code=404, detail="Project not found")
    project_id = existing.id
    await session.delete(existing)
    invalidate_agents(session, project_id=project_id)
