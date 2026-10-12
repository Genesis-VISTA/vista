from typing import Annotated

from fastapi import APIRouter, Form, UploadFile
from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage

from ..agents.skill_authoring import SkillDraft
from ..agents.skills import Skill
from ..db.db import SessionDep
from ..db.schemas import ProjectTable, SkillPublic, SkillUpdate
from ..services import project as project_service
from ..services import skills as skills_service
from ..services.auth import UserDep


router = APIRouter()


class SkillCreate(Skill):
    """The full AgentSkills spec (`Skill`, incl. `body`) plus DB-only hub metadata."""

    author: str | None = None
    repo_url: str | None = None
    is_public: bool = False
    project: str | None = None
    """ Name of a project to load the new skill into. """


class SkillDetail(SkillPublic):
    """A skill's stored metadata plus its SKILL.md body (read from disk)."""

    body: str


class SkillGenerateRequest(BaseModel):
    """
    Body for `POST /skills/generate` — see `generate_skill_draft` for the
    drafting logic. `message_history` uses PydanticAI's `ModelMessage` schema,
    same as `/projects/.../agent/run`.
    """

    message_history: list[ModelMessage] = Field(default_factory=list)
    hint: str | None = None


class SkillImportRequest(BaseModel):
    """
    Body for `POST /skills/import`. `url` accepts either
    `https://github.com/<owner>/<repo>` or
    `https://github.com/<owner>/<repo>/tree/<ref>/<subpath>`.
    """

    url: str
    project: str | None = None
    """ Name of a project to load the imported skill into. """


class SkillPatch(SkillUpdate):
    """
    PATCH body
    """

    body: str | None = None


async def _target_project(
    session: SessionDep, user: UserDep, name: str | None
) -> ProjectTable | None:
    """
    The project a new skill is loaded into, checked before anything is written so
    a missing or forbidden project leaves no skill behind.
    """
    if name is None:
        return None
    return await project_service.get_project_by_name(session, name, user)


def _detail(skill, body: str) -> SkillDetail:
    return SkillDetail.model_validate(
        skill, from_attributes=True, update={"body": body}
    )


@router.get("/skills")
async def list_skills(session: SessionDep) -> list[SkillPublic]:
    """List skill metadata from the DB (no SKILL.md reads)."""
    rows = await skills_service.list_skills(session)
    return [SkillPublic.model_validate(row) for row in rows]


@router.get("/skills/{name}")
async def get_skill(name: str, session: SessionDep) -> SkillDetail:
    skill, body = await skills_service.get_skill_detail(session, name)
    return _detail(skill, body)


@router.post("/skills", status_code=201)
async def create_skill(
    payload: SkillCreate, session: SessionDep, user: UserDep
) -> SkillDetail:
    """Create a new skill (private by default), loaded into `payload.project` if given."""
    project = await _target_project(session, user, payload.project)
    skill = await skills_service.create_skill(
        session,
        spec=payload,
        author=payload.author,
        repo_url=payload.repo_url,
        is_public=payload.is_public,
        project=project,
    )
    # Read the body back from the written SKILL.md rather than echoing the raw request
    _, body_text = await skills_service.get_skill_detail(session, skill.name)
    return _detail(skill, body_text)


@router.post("/skills/generate")
async def generate_skill(body: SkillGenerateRequest, user: UserDep) -> SkillDraft:
    """
    Draft a SKILL.md from a chat conversation. Does NOT persist anything — the
    client edits the draft in a form and then submits `POST /skills` to save.
    """
    return await skills_service.generate_draft(body.message_history, body.hint, user)


@router.post("/skills/import", status_code=201)
async def import_skill(
    body: SkillImportRequest, session: SessionDep, user: UserDep
) -> SkillDetail:
    """
    Import a skill from a public (or token-accessible) GitHub repository, loaded
    into `body.project` if given. Imported skills are private; the user can
    publish later via `PATCH /skills/{name}`.
    """
    project = await _target_project(session, user, body.project)
    skill = await skills_service.import_skill(session, body.url, project)
    _, body_text = await skills_service.get_skill_detail(session, skill.name)
    return _detail(skill, body_text)


@router.post("/skills/import/upload", status_code=201)
async def import_skill_upload(
    files: list[UploadFile],
    paths: Annotated[list[str], Form()],
    session: SessionDep,
    user: UserDep,
    project: Annotated[str | None, Form()] = None,
) -> SkillDetail:
    """
    Import a skill from a local folder the browser uploaded. Multipart body:
    one `files` part per file and a matching `paths` field (in the same order)
    giving each file's path relative to the picked folder. SKILL.md must be at
    the folder's root. Imported skills are private, as with a GitHub import. An
    optional `project` field names a project to load the skill into.
    """
    target = await _target_project(session, user, project)
    skill = await skills_service.import_skill_upload(session, files, paths, target)
    _, body_text = await skills_service.get_skill_detail(session, skill.name)
    return _detail(skill, body_text)


@router.patch("/skills/{name}")
async def patch_skill(
    name: str, payload: SkillPatch, session: SessionDep
) -> SkillDetail:
    skill = await skills_service.update_skill(session, name, payload, body=payload.body)
    _, body_text = await skills_service.get_skill_detail(session, skill.name)
    return _detail(skill, body_text)


@router.delete("/skills/{name}", status_code=204)
async def delete_skill(name: str, session: SessionDep) -> None:
    """Delete a skill"""
    await skills_service.delete_skill(session, name)
