from fastapi import APIRouter

from ..db.db import SessionDep
from ..db.schemas import ProjectCreate, ProjectPublic
from ..services import project as project_service


router = APIRouter(prefix="/projects", tags=["projects"])

@router.get("")
async def list_projects(session: SessionDep) -> list[ProjectPublic]:
    projects = await project_service.list_projects(session)
    return [ProjectPublic.model_validate(p) for p in projects]


@router.get("/{project_name}")
async def get_project(project_name: str, session: SessionDep) -> ProjectPublic:
    project = await project_service.get_project_by_name(session, project_name)
    return ProjectPublic.model_validate(project)


@router.post("", status_code=201)
async def create_project(payload: ProjectCreate, session: SessionDep) -> ProjectPublic:
    project = await project_service.create_project(session, payload)
    return ProjectPublic.model_validate(project)


@router.put("/{project_name}")
async def update_project(project_name: str, updates: ProjectCreate, session: SessionDep) -> ProjectPublic:
    project = await project_service.update_project(session, project_name, updates)
    return ProjectPublic.model_validate(project)


@router.delete("/{project_name}", status_code=204)
async def delete_project(project_name: str, session: SessionDep) -> None:
    await project_service.delete_project(session, project_name)
