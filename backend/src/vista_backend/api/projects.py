from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..agents.forum.project_forum import ForumSetupError, ensure_forum
from ..db.db import SessionDep
from ..db.schemas import ProjectCreate, ProjectPublic, ProjectTable
from ..services import (
    local_settings,
    project as project_service,
    project_collaboration,
)
from ..services.local_settings import LocalSettingsDep


router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("")
async def list_projects(session: SessionDep) -> list[ProjectPublic]:
    projects = await project_service.list_projects(session)
    return [ProjectPublic.model_validate(p) for p in projects]


@router.get("/{project_name}")
async def get_project(project_name: str, session: SessionDep) -> ProjectPublic:
    project = await project_service.get_project_by_name(session, project_name)
    return ProjectPublic.model_validate(project)


async def _open_the_lab(session, project: ProjectTable) -> None:
    """
    Initialise this project's forum, or refuse the save with the reason.

    Done here rather than lazily on first use because the URL is typed into a
    dialog: git accepts any string as a remote, so a typo is not discovered
    until something tries to reach it, and the person who could fix it in a
    second has long since moved on. Syncing now also pulls whatever threads the
    repository already holds, so pointing a project at a forum that exists joins
    that conversation instead of starting an empty one beside it.

    A project with no URL has no lab and nothing to set up. Saving one on a
    machine without a usable git fails here too, saying so. The check reaches the
    remote with the token the forum would push with, so a token that cannot push
    fails the save here too.
    """
    # The row, not the public model: the tokens are only on the row.
    try:
        await ensure_forum(project, await local_settings.get(session))
    except ForumSetupError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("", status_code=201)
async def create_project(payload: ProjectCreate, session: SessionDep) -> ProjectPublic:
    project = await project_service.create_project(session, payload)
    await _open_the_lab(session, project)
    return ProjectPublic.model_validate(project)


@router.put("/{project_name}")
async def update_project(
    project_name: str, updates: ProjectCreate, session: SessionDep
) -> ProjectPublic:
    project = await project_service.update_project(session, project_name, updates)
    await _open_the_lab(session, project)
    return ProjectPublic.model_validate(project)


@router.delete("/{project_name}", status_code=204)
async def delete_project(project_name: str, session: SessionDep) -> None:
    await project_service.delete_project(session, project_name)


class ProjectPublishRequest(BaseModel):
    """Body for `POST /projects/{name}/publish`; empty for an ordinary publish."""

    on_top_of: int | None = Field(default=None, ge=1)
    """
    Publish over this head, which a refused publish reported, rather than over
    the version the project was last published as or imported from.
    """
    as_new: bool = False
    """ Publish as a new project on the server, not as a version of its own. """


@router.post("/{project_name}/publish")
async def publish_project(
    project_name: str,
    body: ProjectPublishRequest,
    session: SessionDep,
    local: LocalSettingsDep,
) -> project_collaboration.ProjectPublishResult:
    """
    Publish the project to the collaboration server, after the skills it loads
    that are not there or have changed. 409 with `code` when the server's head
    has moved on (`version_conflict`, with `head_version`), the item is gone
    (`remote_missing`), or a skill needs a choice first (`skill_conflict`).
    """
    return await project_collaboration.publish_project(
        session, local, project_name, on_top_of=body.on_top_of, as_new=body.as_new
    )
