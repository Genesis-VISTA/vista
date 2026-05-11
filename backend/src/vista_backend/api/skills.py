from fastapi import APIRouter, HTTPException

from ..agents.skills import find_skill_md, find_skills, read_skill, Skill, SkillMetadata
from ..config import settings
from ..utils.misc import path_is_under


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
