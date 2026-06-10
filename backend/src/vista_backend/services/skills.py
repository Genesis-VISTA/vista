"""
Skills service.
"""
# TODO: scope skills to a project; they are global entities for now.
import logging
import shutil
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.skill_authoring import SkillDraft, generate_skill_draft
from ..agents.skill_import import SkillImportError, import_skill_from_github
from ..agents.skills import (
    ParseError,
    Skill,
    find_skill_md,
    parse_skill,
    read_skill,
    skill_to_markdown,
    write_skill,
)
from ..config import settings
from ..db.schemas import SkillTable, SkillUpdate
from ..utils.misc import now_iso
from ._helpers import new_storage_path


logger = logging.getLogger(__name__)


def build_skill_row(
    skill: Skill,
    *,
    path: str,
    author: str | None,
    repo_url: str | None,
    is_public: bool,
    now: str,
) -> SkillTable:
    """ Build a SkillTable row, mirroring spec metadata from the parsed skill. """
    return SkillTable(
        name=skill.name,
        path=path,
        description=skill.description,
        license=skill.license,
        compatibility=skill.compatibility,
        allowed_tools=skill.allowed_tools,
        skill_metadata=skill.metadata,
        author=author,
        repo_url=repo_url,
        is_public=is_public,
        created_at=now,
        updated_at=now,
    )


async def list_skills(session: AsyncSession) -> list[SkillTable]:
    return list((await session.exec(select(SkillTable))).all())


async def get_skill_optional(session: AsyncSession, name: str) -> SkillTable | None:
    return (await session.exec(select(SkillTable).where(SkillTable.name == name))).first()


async def get_skill(session: AsyncSession, name: str) -> SkillTable:
    skill = await get_skill_optional(session, name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {name}")
    return skill


async def get_skill_detail(session: AsyncSession, name: str) -> tuple[SkillTable, str]:
    """ Return the row plus its SKILL.md body (read from disk). """
    skill = await get_skill(session, name)
    return skill, read_skill(settings.data_dir / skill.path).body


async def _insert_skill_row(session: AsyncSession, row: SkillTable) -> None:
    """
    Insert and commit a freshly built skill row, removing its just-written
    folder if the commit fails. The uuid folder belongs solely to this row, so
    cleanup can never clobber another skill's files.
    """
    session.add(row)
    try:
        await session.commit()
    except Exception as e:
        await session.rollback()
        shutil.rmtree(settings.data_dir / row.path, ignore_errors=True)
        if isinstance(e, IntegrityError):
            raise HTTPException(status_code=409, detail=f"Name already in use: {row.name}")
        raise
    await session.refresh(row)


async def create_skill(
    session: AsyncSession,
    *,
    spec: Skill,
    author: str | None = None,
    repo_url: str | None = None,
    is_public: bool = False,
) -> SkillTable:
    """
    Create a skill from a structured `spec` (the server generates the SKILL.md
    frontmatter from its fields). `spec.body` is markdown only.
    """
    if await get_skill_optional(session, spec.name) is not None:
        raise HTTPException(status_code=409, detail=f"Name already in use: {spec.name}")

    path = new_storage_path()
    skill = write_skill(settings.data_dir / path, spec)
    row = build_skill_row(
        skill,
        path=path,
        author=author,
        repo_url=repo_url,
        is_public=is_public,
        now=now_iso(),
    )
    await _insert_skill_row(session, row)
    return row


async def import_skill(session: AsyncSession, url: str) -> SkillTable:
    """
    Import a skill from GitHub. Imported skills are private (`is_public=False`)
    and record the source `url` as their `repo_url`; the user can publish later.
    """
    path = new_storage_path()
    try:
        skill = import_skill_from_github(url, settings.data_dir / path)
    except SkillImportError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if await get_skill_optional(session, skill.name) is not None:
        shutil.rmtree(settings.data_dir / path, ignore_errors=True)
        raise HTTPException(status_code=409, detail=f"Name already in use: {skill.name}")

    row = build_skill_row(skill, path=path, author=None, repo_url=url, is_public=False, now=now_iso())
    await _insert_skill_row(session, row)
    return row


async def generate_draft(message_history: list[ModelMessage], hint: str | None) -> SkillDraft:
    """ Draft a SKILL.md from a chat conversation. Persists nothing. """
    return await generate_skill_draft(message_history, hint)

async def update_skill(
    session: AsyncSession,
    name: str,
    updates: SkillUpdate,
    *,
    body: str | None = None,
) -> SkillTable:
    """
    Partial update. 
    """
    skill = await get_skill(session, name)
    data = updates.model_dump(exclude_unset=True)

    # Reject disallowed transitions up front, before touching disk, so a
    # rejected PATCH never leaves a rewritten SKILL.md behind.
    for field in ("description", "is_public"):
        if field in data and data[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null")
    if data.get("is_public") is False and skill.is_public:
        # Publish is one-way: once listed on the hub a skill can't be hidden again.
        raise HTTPException(status_code=409, detail="Published skills cannot be unpublished.")

    spec_updates = {
        k: data[k]
        for k in ("description", "license", "compatibility", "allowed_tools", "metadata")
        if k in data
    }
    if body is not None:
        spec_updates["body"] = body

    # Remember the SKILL.md we overwrite so we can restore it if the commit fails.
    skill_md_path: Path | None = None
    original_text: str | None = None
    if spec_updates:
        directory = settings.data_dir / skill.path
        skill_md_path = find_skill_md(directory)
        if skill_md_path is None:
            raise ParseError(f"SKILL.md not found in {directory}")
        original_text = skill_md_path.read_text()
        current = parse_skill(original_text)
        # Re-validate the merged result (model_copy skips validators) so a PATCH
        # can't store a value that POST would reject, e.g. an unstripped
        # description. Note the regenerated document keeps only spec frontmatter
        # keys; any extra keys (e.g. in an imported skill) are dropped.
        try:
            merged = Skill.model_validate({**current.model_dump(), **spec_updates})
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))
        skill_md_path.write_text(skill_to_markdown(merged))
        # Re-sync mirrored spec metadata from the regenerated document.
        skill.description = merged.description
        skill.license = merged.license
        skill.compatibility = merged.compatibility
        skill.allowed_tools = merged.allowed_tools
        skill.skill_metadata = merged.metadata

    if "is_public" in data:
        skill.is_public = data["is_public"]
    if "author" in data:
        skill.author = data["author"]
    if "repo_url" in data:
        skill.repo_url = data["repo_url"]

    skill.updated_at = now_iso()
    session.add(skill)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        if skill_md_path is not None and original_text is not None:
            skill_md_path.write_text(original_text)
        raise
    await session.refresh(skill)
    return skill


async def delete_skill(session: AsyncSession, name: str) -> None:
    """
    Delete the skill row, and remove its on-disk directory. Published
    skills are protected (they're listed on the hub).
    """
    skill = await get_skill(session, name)
    if skill.is_public:
        raise HTTPException(status_code=409, detail="Published skills cannot be deleted.")
    directory = settings.data_dir / skill.path
    await session.delete(skill)
    await session.commit()

    try:
        shutil.rmtree(directory)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("Failed to delete skill directory %s: %s", directory, e)
