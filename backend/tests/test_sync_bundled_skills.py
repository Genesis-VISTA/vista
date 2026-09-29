"""
`sync_bundled_skills`: a skill bundled after a deployment was first seeded
still reaches its library on the next startup (`seed_db` alone runs only once,
on an empty database), without touching existing skills or any project.
"""

import shutil

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.config import settings
from vista_backend.db import seed as seed_mod
from vista_backend.db.schemas import ProjectTable, SkillTable
from vista_backend.db.seed import seed_db, sync_bundled_skills

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture
async def engine(tmp_path, monkeypatch):
    """An offline-seeded in-memory DB, as an existing deployment would have."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "vista_data_token", None)
    monkeypatch.setattr(settings, "vista_data_payload_dir", None)
    eng = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    await seed_db(eng)
    yield eng
    await eng.dispose()


async def _skills(engine) -> dict[str, SkillTable]:
    async with AsyncSession(engine) as session:
        return {s.name: s for s in (await session.exec(select(SkillTable))).all()}


async def _forget(engine, name: str) -> None:
    """Simulate a DB seeded before `name` was bundled."""
    async with AsyncSession(engine) as session:
        await session.exec(delete(SkillTable).where(SkillTable.name == name))
        await session.commit()


async def test_fresh_seed_leaves_nothing_to_sync(engine):
    assert await sync_bundled_skills(engine) == []


async def test_skill_bundled_after_seeding_is_registered_once(engine):
    await _forget(engine, "llm-pretraining")
    assert "llm-pretraining" not in await _skills(engine)

    assert await sync_bundled_skills(engine) == ["llm-pretraining"]
    row = (await _skills(engine))["llm-pretraining"]
    assert row.is_public
    assert row.author == "VISTA Team"
    copied = settings.data_dir / row.path
    assert (copied / "SKILL.md").is_file()
    assert (copied / "scripts" / "plot_training.py").is_file()
    assert not list(copied.rglob("__pycache__"))

    assert await sync_bundled_skills(engine) == []  # idempotent


async def test_sync_changes_no_project(engine):
    async with AsyncSession(engine) as session:
        before = {
            p.name: list(p.skills)
            for p in (await session.exec(select(ProjectTable))).all()
        }
    await _forget(engine, "llm-pretraining")
    await sync_bundled_skills(engine)
    async with AsyncSession(engine) as session:
        after = {
            p.name: list(p.skills)
            for p in (await session.exec(select(ProjectTable))).all()
        }
    assert after == before
    assert all("llm-pretraining" not in skills for skills in after.values())


async def test_existing_skill_rows_are_left_alone(engine):
    # Any skill the first seed registered -- not a named one, since the bundled
    # set changes as skills are added and retired.
    async with AsyncSession(engine) as session:
        row = (await session.exec(select(SkillTable).order_by(SkillTable.name))).first()
        assert row is not None, "the offline seed registered no skills"
        name = row.name
        row.description = "edited by an admin"
        session.add(row)
        await session.commit()
    await sync_bundled_skills(engine)
    assert (await _skills(engine))[name].description == "edited by an admin"


async def test_asset_skills_are_left_to_seed_db(engine):
    # Offline, the first seed skipped them for lack of assets; sync must not
    # register them without their data either.
    names = await _skills(engine)
    for name in seed_mod.SKILL_ASSETS:
        assert name not in names
    await sync_bundled_skills(engine)
    names = await _skills(engine)
    for name in seed_mod.SKILL_ASSETS:
        assert name not in names


async def test_folder_without_skill_md_is_ignored(engine, tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    (bundled / "leftover" / "__pycache__").mkdir(parents=True)
    real = seed_mod.SKILLS_SRC / "llm-pretraining"
    monkeypatch.setattr(seed_mod, "SKILLS_SRC", bundled)
    shutil.copytree(real, bundled / "llm-pretraining")
    await _forget(engine, "llm-pretraining")
    assert await sync_bundled_skills(engine) == ["llm-pretraining"]
