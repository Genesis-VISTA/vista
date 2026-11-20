"""
A project's linked folders (openspec change linked-folders): list, link, rename, unlink.

Every change evicts the project's pooled agents once it commits, so each conversation
mounts the new set from its next turn; a turn or background command already running holds
its agent, and keeps its mounts, until it ends.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from ..db.db import SessionDep
from ..services import linked_folders, project as project_service
from ..services.linked_folders import UnavailableReason
from ..services.project_agent import invalidate_agents

router = APIRouter(prefix="/projects/{project_name}/linked-folders", tags=["projects"])


class LinkedFolderPublic(BaseModel):
    name: str
    host_path: str
    sandbox_path: str
    available: bool
    reason: UnavailableReason | None = None
    """ Why a sandbox starting now could not mount it; None when it could. """


class LinkFolderRequest(BaseModel):
    path: str
    """ The folder's absolute path on this computer, picked or typed. """
    name: str | None = None
    """ Its name in the sandbox; by default, made from the folder's own. """


class RenameLinkRequest(BaseModel):
    name: str


def _public(row) -> LinkedFolderPublic:
    return LinkedFolderPublic(**vars(linked_folders.status_of(row)))


@router.get("")
async def list_linked_folders(
    project_name: str, session: SessionDep
) -> list[LinkedFolderPublic]:
    project = await project_service.get_project_by_name(session, project_name)
    return [_public(r) for r in await linked_folders.list_links(session, project.id)]


@router.post("", status_code=201)
async def link_folder(
    project_name: str, body: LinkFolderRequest, session: SessionDep
) -> LinkedFolderPublic:
    project = await project_service.get_project_by_name(session, project_name)
    row = await linked_folders.add_link(session, project.id, body.path, body.name)
    invalidate_agents(session, project_id=project.id)
    return _public(row)


@router.patch("/{link_name}")
async def rename_linked_folder(
    project_name: str, link_name: str, body: RenameLinkRequest, session: SessionDep
) -> LinkedFolderPublic:
    project = await project_service.get_project_by_name(session, project_name)
    row = await linked_folders.rename_link(session, project.id, link_name, body.name)
    invalidate_agents(session, project_id=project.id)
    return _public(row)


@router.delete("/{link_name}", status_code=204)
async def unlink_folder(project_name: str, link_name: str, session: SessionDep) -> None:
    project = await project_service.get_project_by_name(session, project_name)
    await linked_folders.remove_link(session, project.id, link_name)
    invalidate_agents(session, project_id=project.id)
