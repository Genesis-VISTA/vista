from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage

from ..agents.skills import (
    find_skill_md,
    find_skills,
    read_skill,
    update_skill_frontmatter,
    write_skill,
    Skill,
    SkillError,
    SkillMetadata,
)
from ..agents.skill_authoring import SkillDraft, generate_skill_draft
from ..agents.skill_import import SkillImportError, import_skill_from_github
from ..config import settings
from ..utils.misc import path_is_under


router = APIRouter()

# TODO: should make this a full CRUD so you can create and update your own skills
# Skills stored in the database as zip blobs?
# Also need to work on how skills are mounted into the container now that you can filter available
# skills (and when we separate project containers)

class SkillCreate(BaseModel):
    name: str
    description: str
    body: str
    author: str | None = None
    repo_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    is_public: bool = False


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


@router.get("/skills")
async def list_skills() -> list[SkillMetadata]:
    skills_root = settings.skills_dir
    response: list[SkillMetadata] = []
    for skill_dir in find_skills([skills_root]):
        skill = read_skill(skill_dir)
        response.append(SkillMetadata.model_validate(skill, from_attributes=True))
    return response


@router.get("/skills/{name}")
async def get_skill(name: str) -> Skill:
    skill_dir = settings.skills_dir / name
    if not path_is_under(settings.skills_dir, skill_dir):
        raise HTTPException(status_code=404, detail="Skill not found")
    if find_skill_md(skill_dir) is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return read_skill(skill_dir)


class SkillPatch(BaseModel):
    is_public: bool | None = None
    author: str | None = None
    repo_url: str | None = None


@router.post("/skills", status_code=201)
async def create_skill(body: SkillCreate) -> Skill:
    """Create a new skill on disk (private by default)."""
    try:
        return write_skill(
            settings.skills_dir,
            name=body.name,
            description=body.description,
            body=body.body,
            author=body.author,
            repo_url=body.repo_url,
            tags=body.tags,
            is_public=body.is_public,
        )
    except FileExistsError:
        raise HTTPException(
            status_code=409,
            detail=f"A skill named {body.name!r} already exists.",
        )
    except SkillError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/skills/generate")
async def generate_skill(body: SkillGenerateRequest) -> SkillDraft:
    """
    Draft a SKILL.md from a chat conversation. Does NOT persist anything — the
    client edits the draft in a form and then submits `POST /skills` to save.
    """
    return await generate_skill_draft(body.message_history, body.hint)


@router.post("/skills/import", status_code=201)
async def import_skill(body: SkillImportRequest) -> Skill:
    """
    Import a skill from a public (or token-accessible) GitHub repository.
    Imported skills are forced `is_public:false`; the user can publish later
    via `PATCH /skills/{name}`.
    """
    try:
        dest_dir = import_skill_from_github(body.url, settings.skills_dir)
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except SkillImportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return read_skill(dest_dir)


@router.patch("/skills/{name}")
async def patch_skill(name: str, body: SkillPatch) -> Skill:
    skill_dir = settings.skills_dir / name
    if not path_is_under(settings.skills_dir, skill_dir):
        raise HTTPException(status_code=404, detail="Skill not found")
    if find_skill_md(skill_dir) is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return read_skill(skill_dir)
    # Publish is one-way: once a skill is published to the hub, it cannot be
    # made private again. Reject the request rather than silently no-op so the
    # UI can show a clear error.
    if "is_public" in updates and updates["is_public"] is False:
        current = read_skill(skill_dir)
        if current.is_public:
            raise HTTPException(
                status_code=409,
                detail="Published skills cannot be unpublished.",
            )
    return update_skill_frontmatter(skill_dir, updates)
