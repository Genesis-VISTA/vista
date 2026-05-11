import uuid
from fastapi import APIRouter, HTTPException
from sqlmodel import select

from ..db.db import SessionDep
from ..db.schemas import ProjectCreate, ProjectPublic, ProjectTable


router = APIRouter(prefix="/projects", tags=["projects"])

@router.get("")
async def list_projects(session: SessionDep) -> list[ProjectPublic]:
    projects = (await session.exec(select(ProjectTable))).all()
    return [ProjectPublic.model_validate(p) for p in projects]


@router.get("/{project_id}")
async def get_project(project_id: uuid.UUID, session: SessionDep) -> ProjectPublic:
    project = await session.get(ProjectTable, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectPublic.model_validate(project)


@router.post("", status_code=201)
async def create_project(payload: ProjectCreate, session: SessionDep) -> ProjectPublic:
    new = ProjectTable.model_validate(payload)
    session.add(new)
    await session.flush()
    await session.refresh(new)
    return ProjectPublic.model_validate(new)


@router.put("/{project_id}")
async def update_project(project_id: uuid.UUID, updates: ProjectCreate, session: SessionDep) -> ProjectPublic:
    project = await session.get(ProjectTable, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    for key, value in updates.model_dump().items():
        setattr(project, key, value)
    session.add(project)
    await session.flush()
    await session.refresh(project)
    return ProjectPublic.model_validate(project)


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: uuid.UUID, session: SessionDep) -> None:
    existing = await session.get(ProjectTable, project_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Project not found")
    await session.delete(existing)
