"""
Seed project snapshots for `alloy-design` and `molten-salt` (Milestone C).

Asserts the offline seed (no vista-data token) wires the expected skill slugs
and tool patterns. Seed data lives inline in `seed.py` — there is no
`defaults.py`. Complements `test_seed_splash.py`, which only covers the
SPLASH-into-molten-salt fold.
"""

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.config import settings
from vista_backend.db.schemas import KnowledgeBaseTable, ProjectTable, SkillTable
from vista_backend.db.seed import seed_db

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture
async def seeded(tmp_path, monkeypatch):
    """Offline seed into a throwaway data dir + in-memory SQLite."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "vista_data_token", None)

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fks(dbapi_connection, _):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    await seed_db(engine)
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


async def _project(session, name: str) -> ProjectTable:
    row = (
        await session.exec(select(ProjectTable).where(ProjectTable.name == name))
    ).first()
    assert row is not None, f"seed did not create project {name!r}"
    return row


async def test_seed_registers_skills_that_do_not_need_vista_data(seeded):
    """Skills with no GitLab assets are always registered offline."""
    names = {s.name for s in (await seeded.exec(select(SkillTable))).all()}
    assert {
        "alloy-tc-planner",
        "alloy-thermo-mc",
        "deepthermo-wl",
        "vae-orderparam",
        "datacard-generation",
        "salt-chemistry-md",
        "salt-neutronics-tbr",
        "splash-planner",
    }.issubset(names)
    # Asset-dependent skills are skipped without a vista-data token.
    assert "salt-analysis" not in names
    assert "model-fine-tuning" not in names
    assert "salt-prediction" not in names


async def test_alloy_design_seed_snapshot(seeded):
    project = await _project(seeded, "alloy-design")

    assert project.skills == [
        "alloy-tc-planner",
        "alloy-thermo-mc",
        "deepthermo-wl",
        "vae-orderparam",
    ]
    assert project.knowledge_bases == []
    assert project.usage_limits.get("request_limit") == 600

    # The campaign drives the STANDARD HPC toolchain, so those must be allowed; the
    # retired agenthpc_* SSH tools are denied.
    assert "*" in project.tools
    assert "!agenthpc_*" in project.tools
    assert "!submit_hpc_job" not in project.tools
    assert "!get_hpc_job_status" not in project.tools
    assert "!get_hpc_job_outputs" not in project.tools
    assert "!list_hpc_jobs" not in project.tools

    assert (
        "alloy" in project.system_prompt.lower() or "MoNbTaW" in project.system_prompt
    )
    # The retired agenthpc_* loop must be gone from the prompt.
    assert "agenthpc_" not in project.system_prompt

    # Both skills the project references are registered.
    skill_names = {s.name for s in (await seeded.exec(select(SkillTable))).all()}
    assert set(project.skills).issubset(skill_names)


async def test_molten_salt_seed_snapshot_offline(seeded):
    project = await _project(seeded, "molten-salt")

    # Offline: salt-analysis is skipped; the other three remain.
    assert set(project.skills) == {
        "salt-chemistry-md",
        "salt-neutronics-tbr",
        "splash-planner",
    }
    # No KB without vista-data.
    assert project.knowledge_bases == []
    assert project.usage_limits.get("request_limit") == 100

    # Standard HPC tools are allowed; the alloy-design agenthpc_* tools are not.
    assert "*" in project.tools
    assert "!agenthpc_*" in project.tools
    assert "!submit_hpc_job" not in project.tools

    assert "SPLASH" in project.system_prompt
    assert "TBR" in project.system_prompt or "tritium" in project.system_prompt.lower()

    skill_names = {s.name for s in (await seeded.exec(select(SkillTable))).all()}
    assert set(project.skills).issubset(skill_names)


async def test_offline_seed_does_not_create_a_knowledge_base(seeded):
    rows = (await seeded.exec(select(KnowledgeBaseTable))).all()
    assert rows == []


async def test_seed_project_ids_are_stable(seeded):
    """Fixed UUIDs keep external references (docs, fixtures) stable across re-seeds."""
    alloy = await _project(seeded, "alloy-design")
    molten = await _project(seeded, "molten-salt")
    assert str(alloy.id) == "f855bdd8-c433-423e-ab5c-3a9a63b6e661"
    assert str(molten.id) == "282531e7-1e05-4369-a339-9d1b4f20aa89"
