from fastapi import APIRouter
from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage

from ..agents.skill_authoring import SkillDraft
from ..agents.skills import Skill
from ..db.db import SessionDep
from ..db.schemas import SkillPublic, SkillUpdate
from ..services import skills as skills_service
from ..services.auth import UserDep


router = APIRouter()


class SkillCreate(Skill):
    """The full AgentSkills spec (`Skill`, incl. `body`) plus DB-only hub metadata."""

    author: str | None = None
    repo_url: str | None = None
    is_public: bool = False


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


class SkillPatch(SkillUpdate):
    """
    PATCH body
    """

    body: str | None = None


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
async def create_skill(payload: SkillCreate, session: SessionDep) -> SkillDetail:
    """Create a new skill (private by default)."""
    skill = await skills_service.create_skill(
        session,
        spec=payload,
        author=payload.author,
        repo_url=payload.repo_url,
        is_public=payload.is_public,
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
async def import_skill(body: SkillImportRequest, session: SessionDep) -> SkillDetail:
    """
    Import a skill from a public (or token-accessible) GitHub repository.
    Imported skills are private; the user can publish later via `PATCH /skills/{name}`.
    """
    skill = await skills_service.import_skill(session, body.url)
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
