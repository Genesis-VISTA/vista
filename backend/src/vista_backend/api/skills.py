import uuid
from typing import Annotated

from fastapi import APIRouter, Form, UploadFile
from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage

from ..agents.skill_authoring import SkillDraft
from ..agents.skills import Skill, skill_hpc_jobs
from ..config import settings
from ..db.db import SessionDep
from ..db.schemas import ProjectTable, SkillPublic, SkillUpdate
from ..services import project as project_service
from ..services.local_settings import LocalSettingsDep
from ..services import skills as skills_service


router = APIRouter()


class SkillCreate(Skill):
    """The full AgentSkills spec (`Skill`, incl. `body`) plus DB-only hub metadata."""

    author: str | None = None
    repo_url: str | None = None
    is_public: bool = False
    project: str | None = None
    """ Name of a project to load the new skill into. """


class SkillListItem(SkillPublic):
    """A skill's stored metadata plus what its folder ships (read from disk)."""

    hpc_jobs: list[str] = Field(default_factory=list)
    """
    HPC jobs under the skill's `hpc_jobs/`. A project that loads the skill lets
    the agent submit them (as `<skill>/<job>`) under the researcher's cluster
    credentials, so the UI marks such a skill.
    """


class SkillDetail(SkillListItem):
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
    chat_session_id: uuid.UUID | None = None
    """ The chat the draft is from; its own model, if it has one, writes the draft. """


class SkillImportRequest(BaseModel):
    """
    Body for `POST /skills/import`: the URL of a repository on GitHub or any
    GitLab holding one skill, or of one skill's folder in it
    (`.../tree/<ref>/<path>`, `.../-/tree/<ref>/<path>`).
    """

    url: str
    project: str | None = None
    """ Name of a project to load the imported skill into. """
    token: str | None = None
    """ A token for this import only, sent to the URL's host and never stored. """
    on_conflict: skills_service.OnConflict | None = None
    rename_to: str | None = None


class SkillScanRequest(BaseModel):
    """Body for `POST /skills/import/scan`."""

    url: str
    token: str | None = None
    """ A token for this scan only, sent to the URL's host and never stored. """


class SkillBatchImportRequest(BaseModel):
    """Body for `POST /skills/import/batch`: skills a scan of `url` listed."""

    url: str
    skills: list[skills_service.SkillChoice]
    commit: str | None = None
    """ The commit the scan saw, so the skills installed are the ones listed. """
    project: str | None = None
    token: str | None = None


class SkillImportResultOut(BaseModel):
    path: str
    name: str | None
    status: str
    detail: str | None = None
    skill: SkillListItem | None = None


class SkillFromOutputRequest(BaseModel):
    """
    Body for `POST /projects/{project_name}/skills/from-output`: a folder under
    the caller's `/mnt/data/output` in that project, as listed by the GET.
    """

    folder: str
    replace: bool = False
    """ Swap the files of an existing private skill of the same name. """


class SkillPatch(SkillUpdate):
    """
    PATCH body
    """

    body: str | None = None


def _hpc_jobs(skill) -> list[str]:
    return skill_hpc_jobs(settings.data_dir / skill.path)


async def _target_project(session: SessionDep, name: str | None) -> ProjectTable | None:
    """
    The project a new skill is loaded into, checked before anything is written so
    a missing or forbidden project leaves no skill behind.
    """
    if name is None:
        return None
    return await project_service.get_project_by_name(session, name)


def _detail(skill, body: str) -> SkillDetail:
    return SkillDetail.model_validate(
        skill, from_attributes=True, update={"body": body, "hpc_jobs": _hpc_jobs(skill)}
    )


@router.get("/skills")
async def list_skills(session: SessionDep) -> list[SkillListItem]:
    """List skill metadata from the DB (no SKILL.md reads), plus each skill's HPC jobs."""
    rows = await skills_service.list_skills(session)
    return [
        SkillListItem.model_validate(
            row, from_attributes=True, update={"hpc_jobs": _hpc_jobs(row)}
        )
        for row in rows
    ]


@router.get("/skills/{name}")
async def get_skill(name: str, session: SessionDep) -> SkillDetail:
    skill, body = await skills_service.get_skill_detail(session, name)
    return _detail(skill, body)


@router.post("/skills", status_code=201)
async def create_skill(payload: SkillCreate, session: SessionDep) -> SkillDetail:
    """Create a new skill (private by default), loaded into `payload.project` if given."""
    project = await _target_project(session, payload.project)
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
async def generate_skill(
    body: SkillGenerateRequest, session: SessionDep, local: LocalSettingsDep
) -> SkillDraft:
    """
    Draft a SKILL.md from a chat conversation. Does NOT persist anything — the
    client edits the draft in a form and then submits `POST /skills` to save.
    """
    return await skills_service.generate_draft(
        body.message_history,
        body.hint,
        local,
        session=session,
        chat_session_id=body.chat_session_id,
    )


@router.post("/skills/import", status_code=201)
async def import_skill(body: SkillImportRequest, session: SessionDep) -> SkillDetail:
    """
    Import the one skill a repository URL names, loaded into `body.project` if
    given. A repository with several skills is refused (400) with their names;
    scan it and import through `/skills/import/batch`. Private repositories need
    `body.token`, or the Settings token for github.com or gitlab.com.
    Imported skills are private; the researcher can publish later via
    `PATCH /skills/{name}`.
    """
    project = await _target_project(session, body.project)
    skill = await skills_service.import_skill(
        session,
        body.url,
        project,
        token=body.token,
        on_conflict=body.on_conflict,
        rename_to=body.rename_to,
    )
    _, body_text = await skills_service.get_skill_detail(session, skill.name)
    return _detail(skill, body_text)


@router.post("/skills/import/scan")
async def scan_skill_repository(
    body: SkillScanRequest, session: SessionDep
) -> skills_service.RepoScan:
    """
    List the skills in a repository, or in the folder of it the URL links to:
    name, description and what each reaches outside its folder, and whether its
    name is taken. Imports nothing.
    """
    return await skills_service.scan_repository(session, body.url, token=body.token)


@router.post("/skills/import/batch")
async def import_skill_batch(
    body: SkillBatchImportRequest, session: SessionDep
) -> list[SkillImportResultOut]:
    """
    Import the chosen skills of a scanned repository, each with what to do if
    its name is taken (skip, overwrite a private skill, or rename). One result
    per skill, in order; a failure does not stop the rest.
    """
    project = await _target_project(session, body.project)
    results = await skills_service.import_repository_skills(
        session,
        body.url,
        body.skills,
        project,
        token=body.token,
        commit=body.commit,
    )
    return [
        SkillImportResultOut(
            path=r.path,
            name=r.name,
            status=r.status,
            detail=r.detail,
            skill=SkillListItem.model_validate(
                r.skill, from_attributes=True, update={"hpc_jobs": _hpc_jobs(r.skill)}
            )
            if r.skill is not None
            else None,
        )
        for r in results
    ]


@router.post("/skills/import/upload", status_code=201)
async def import_skill_upload(
    files: list[UploadFile],
    paths: Annotated[list[str], Form()],
    session: SessionDep,
    project: Annotated[str | None, Form()] = None,
) -> SkillDetail:
    """
    Import a skill from a local folder the browser uploaded. Multipart body:
    one `files` part per file and a matching `paths` field (in the same order)
    giving each file's path relative to the picked folder. SKILL.md must be at
    the folder's root. Imported skills are private, as with a GitHub import. An
    optional `project` field names a project to load the skill into.
    """
    target = await _target_project(session, project)
    skill = await skills_service.import_skill_upload(session, files, paths, target)
    _, body_text = await skills_service.get_skill_detail(session, skill.name)
    return _detail(skill, body_text)


@router.get("/projects/{project_name}/skills/from-output")
async def list_output_skills(
    project_name: str, session: SessionDep
) -> list[skills_service.OutputSkill]:
    """
    The skills the agent wrote to the output folder in this project:
    top-level folders of `/mnt/data/output` that hold a SKILL.md.
    """
    return await skills_service.list_output_skills(session, project_name)


@router.post("/projects/{project_name}/skills/from-output", status_code=201)
async def import_output_skill(
    project_name: str,
    body: SkillFromOutputRequest,
    session: SessionDep,
) -> SkillDetail:
    """
    Import a skill from the output folder in this project, with the
    same checks as a folder upload, and load it into the project. Private, as
    with any import.
    """
    skill = await skills_service.import_skill_from_output(
        session, project_name, body.folder, replace=body.replace
    )
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
