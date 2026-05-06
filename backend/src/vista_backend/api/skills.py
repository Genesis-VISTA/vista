from fastapi import APIRouter, HTTPException

from ..agents.skills import find_skill_md, find_skills, read_skill, Skill, SkillMetadata
from ..config import settings


router = APIRouter()

# TODO: should make this a full CRUD so you can create and update your own skills
# Skills stored in the database as zip blobs?
# Also need to work on how skills are mounted into the container now that you can filter available
# skills (and when we separate project containers)

@router.get("/skills")
async def list_skills() -> list[SkillMetadata]:
    skills_root = settings.skills_dir
    response: list[SkillMetadata] = []
    for skill_dir in find_skills([skills_root]):
        response.append(read_skill(skill_dir))
    # FastAPI will strip the body param from result, since return type is SkillMetadata
    return response


@router.get("/skills/{name}")
async def get_skill(name: str) -> Skill:
    skill_md = find_skill_md(settings.skills_dir / name)
    if skill_md is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return read_skill(skill_md)
