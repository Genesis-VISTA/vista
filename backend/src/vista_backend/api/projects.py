import uuid
from fastapi import APIRouter, HTTPException
from sqlmodel import select

from ..db.db import SessionDep
from ..db.schemas import ProjectCreate, ProjectPublic, ProjectTable


router = APIRouter(prefix="/projects", tags=["projects"])

@router.get("")
def list_projects(session: SessionDep) -> list[ProjectPublic]:
    projects = session.exec(select(ProjectTable)).all()
    return [ProjectPublic.model_validate(p) for p in projects]


@router.get("/{project_id}")
def get_project(project_id: uuid.UUID, session: SessionDep) -> ProjectPublic:
    project = session.get(ProjectTable, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectPublic.model_validate(project)


@router.post("", status_code=201)
def create_project(payload: ProjectCreate, session: SessionDep) -> ProjectPublic:
    new = ProjectTable.model_validate(payload)
    session.add(new)
    session.flush()
    session.refresh(new)
    return ProjectPublic.model_validate(new)


@router.patch("/{project_id}")
def update_project(project_id: uuid.UUID, updates: ProjectCreate, session: SessionDep) -> ProjectPublic:
    project = session.get(ProjectTable, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    for key, value in updates.model_dump():
        setattr(project, key, value)
    session.add(project)
    session.flush()
    session.refresh(project)
    return ProjectPublic.model_validate(project)


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: uuid.UUID, session: SessionDep) -> None:
    existing = session.get(ProjectTable, project_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Project not found")
    session.delete(existing)
