from fastapi import APIRouter
from pydantic import BaseModel

from ..db.db import SessionDep
from ..db.schemas import ProjectCreate, ProjectPublic, UserPublic
from ..services import project as project_service
from .auth import UserDep


router = APIRouter(prefix="/projects", tags=["projects"])

@router.get("")
async def list_projects(session: SessionDep, user: UserDep) -> list[ProjectPublic]:
    projects = await project_service.list_projects(session, user)
    return [ProjectPublic.model_validate(p) for p in projects]


@router.get("/{project_name}")
async def get_project(project_name: str, session: SessionDep, user: UserDep) -> ProjectPublic:
    project = await project_service.get_project_by_name(session, project_name, user)
    return ProjectPublic.model_validate(project)


@router.post("", status_code=201)
async def create_project(payload: ProjectCreate, session: SessionDep, user: UserDep) -> ProjectPublic:
    project = await project_service.create_project(session, payload, user)
    return ProjectPublic.model_validate(project)


@router.put("/{project_name}")
async def update_project(
    project_name: str, updates: ProjectCreate, session: SessionDep, user: UserDep
) -> ProjectPublic:
    project = await project_service.update_project(session, project_name, updates, user)
    return ProjectPublic.model_validate(project)


@router.delete("/{project_name}", status_code=204)
async def delete_project(project_name: str, session: SessionDep, user: UserDep) -> None:
    await project_service.delete_project(session, project_name, user)


@router.get("/{project_name}/members")
async def list_members(project_name: str, session: SessionDep, user: UserDep) -> list[UserPublic]:
    members = await project_service.list_project_members(session, project_name, user)
    return [UserPublic.model_validate(m) for m in members]


class AddMemberRequest(BaseModel):
    email: str


@router.post("/{project_name}/members", status_code=201)
async def add_member(
    project_name: str, body: AddMemberRequest, session: SessionDep, user: UserDep
) -> None:
    await project_service.add_project_member(session, project_name, body.email, user)
