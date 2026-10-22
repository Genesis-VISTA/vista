import uuid
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..db.db import SessionDep
from ..services import collaboration, skill_collaboration
from ..services.local_settings import LocalSettingsDep
from .skills import SkillListItem, list_item, target_project

router = APIRouter(prefix="/collaboration", tags=["collaboration"])


@router.get("/status")
async def get_status(local: LocalSettingsDep) -> collaboration.CollaborationStatus:
    """
    Whether publishing and importing can work: a server is set and answers. Checked
    on every call, in at most a few seconds; `reason` says why not when they cannot.
    """
    return await collaboration.status(local)


@router.get("/skills")
async def list_skills(
    session: SessionDep, local: LocalSettingsDep, manifests: bool = True
) -> list[skill_collaboration.RemoteSkill]:
    """
    The collaboration server's skills, oldest first, each with this
    installation's copy of it, if any, and what its newest version says about
    it. `manifests=false` leaves that out, for one request to the server
    instead of one per skill.
    """
    return await skill_collaboration.list_remote_skills(
        session, local, manifests=manifests
    )


class RemoteSkillImportRequest(BaseModel):
    """Body for `POST /collaboration/skills/{item_id}/import`."""

    version: int | None = Field(default=None, ge=1)
    """ The version to import; the newest by default. """
    rename_to: str | None = None
    """ The name for a new skill whose own is taken here. """
    replace_modified: bool = False
    """ Replace the changes made here to the copy already imported. """
    project: str | None = None
    """ Name of a project to load the skill into. """


class RemoteSkillImportResult(BaseModel):
    status: Literal["imported", "updated", "unchanged"]
    skill: SkillListItem


@router.post("/skills/{item_id}/import")
async def import_skill(
    item_id: uuid.UUID,
    body: RemoteSkillImportRequest,
    session: SessionDep,
    local: LocalSettingsDep,
) -> RemoteSkillImportResult:
    """
    Import a skill from the collaboration server, updating the copy already
    here if there is one. 409 with `code` when the copy here has changes of its
    own (`modified`) or the name is another skill's (`name_taken`).
    """
    project = await target_project(session, body.project)
    skill, status = await skill_collaboration.import_skill(
        session,
        local,
        item_id,
        version=body.version,
        rename_to=body.rename_to,
        replace_modified=body.replace_modified,
        project=project,
    )
    return RemoteSkillImportResult(status=status, skill=list_item(skill))
