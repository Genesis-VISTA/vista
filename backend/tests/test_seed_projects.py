"""
Seed project snapshots for `alloy-design` and `molten-salt` (Milestone C).

Parametrized over `seed_science_projects`: off seeds neither project and skips the
MSTDB-dependent skills; on asserts the expected skill slugs and tool patterns. Seed data lives inline in `seed.py` — there is no
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
from vista_backend.db import seed as seed_module
from vista_backend.db.seed import LocalRepoClient, seed_db

from test_seed_payload import make_payload, make_vector_store

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def _seed(engine):
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    await seed_db(engine)


@pytest.fixture(params=["off", "on"])
async def seeded(request, tmp_path, monkeypatch):
    """
    Seed into a throwaway data dir + in-memory SQLite, with the science projects
    off (no vista-data at all) or on (a token whose client reads a payload tree
    in `tmp_path`, with a prebuilt index so nothing is embedded).
    """
    data_dir = tmp_path / "data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "vista_data_token", None)
    monkeypatch.setattr(settings, "vista_data_payload_dir", None)
    monkeypatch.setattr(settings, "seed_science_projects", False)
    monkeypatch.setattr(seed_module, "REPO_ROOT", tmp_path / "repo")

    if request.param == "on":
        payload = make_payload(tmp_path / "payload")
        make_vector_store(
            data_dir / "knowledge-bases" / "molten-salt-papers" / "rag_db"
        )
        monkeypatch.setattr(settings, "vista_data_token", "glpat-stub")
        monkeypatch.setattr(settings, "seed_science_projects", True)
        monkeypatch.setattr(
            seed_module,
            "GitlabRepoClient",
            lambda domain, repo, token=None: LocalRepoClient(payload),
        )

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

    await _seed(engine)
    async with AsyncSession(engine) as session:
        session.science = request.param == "on"
        yield session
    await engine.dispose()


@pytest.fixture
def science_on(seeded):
    if not seeded.science:
        pytest.skip("science projects are off in this variant")
    return seeded


@pytest.fixture
def science_off(seeded):
    if seeded.science:
        pytest.skip("science projects are on in this variant")
    return seeded


async def _project(session, name: str) -> ProjectTable:
    row = (
        await session.exec(select(ProjectTable).where(ProjectTable.name == name))
    ).first()
    assert row is not None, f"seed did not create project {name!r}"
    return row


NEEDS_MSTDB = {"salt-analysis", "model-fine-tuning", "salt-prediction"}


async def _skill_names(session) -> set[str]:
    return {s.name for s in (await session.exec(select(SkillTable))).all()}


async def test_every_bundled_skill_without_mstdb_is_always_registered(seeded):
    """The flag gates the science projects, not the skill library."""
    expected = {src.name for src in seed_module._bundled_skill_dirs()} - set(
        seed_module.SKILL_ASSETS
    )
    assert expected.issubset(await _skill_names(seeded))
    assert {"salt-chemistry-md", "splash-planner", "alloy-tc-planner"} <= expected


async def test_science_off_seeds_no_science_rows(science_off):
    names = {p.name for p in (await science_off.exec(select(ProjectTable))).all()}
    assert names.isdisjoint({"molten-salt", "alloy-design"})
    assert (await science_off.exec(select(KnowledgeBaseTable))).all() == []
    # MSTDB-dependent skills are skipped exactly as they are without a token.
    assert NEEDS_MSTDB.isdisjoint(await _skill_names(science_off))


async def test_science_off_does_not_fetch_science_data(tmp_path, monkeypatch):
    """With a token but the flag off, nothing is requested from vista-data."""
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "vista_data_token", "glpat-stub")
    monkeypatch.setattr(settings, "vista_data_payload_dir", None)
    monkeypatch.setattr(settings, "seed_science_projects", False)
    monkeypatch.setattr(seed_module, "REPO_ROOT", tmp_path / "repo")

    class Forbidden:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        def __getattr__(self, name):
            raise AssertionError(f"vista-data was asked for {name}")

    monkeypatch.setattr(
        seed_module, "GitlabRepoClient", lambda domain, repo, token=None: Forbidden()
    )
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        await _seed(engine)
        async with AsyncSession(engine) as session:
            assert (await session.exec(select(ProjectTable))).all() == []
            assert NEEDS_MSTDB.isdisjoint(await _skill_names(session))
    finally:
        await engine.dispose()


async def test_science_on_registers_mstdb_skills(science_on):
    assert NEEDS_MSTDB <= await _skill_names(science_on)


async def test_alloy_design_seed_snapshot(science_on):
    project = await _project(science_on, "alloy-design")

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

    assert (
        "alloy" in project.system_prompt.lower() or "MoNbTaW" in project.system_prompt
    )
    # The retired agenthpc_* loop must be gone from the prompt.
    assert "agenthpc_" not in project.system_prompt

    # Both skills the project references are registered.
    assert set(project.skills).issubset(await _skill_names(science_on))


async def test_molten_salt_seed_snapshot(science_on):
    project = await _project(science_on, "molten-salt")

    assert set(project.skills) == {
        "salt-analysis",
        "salt-chemistry-md",
        "salt-neutronics-tbr",
        "splash-planner",
    }
    assert project.knowledge_bases == ["molten-salt-papers"]
    assert project.usage_limits.get("request_limit") == 100

    # Standard HPC tools are allowed; the alloy-design agenthpc_* tools are not.
    assert "*" in project.tools
    assert "!agenthpc_*" in project.tools
    assert "!submit_hpc_job" not in project.tools

    assert "SPLASH" in project.system_prompt
    assert "TBR" in project.system_prompt or "tritium" in project.system_prompt.lower()

    assert set(project.skills).issubset(await _skill_names(science_on))


async def test_science_on_seeds_the_molten_salt_knowledge_base(science_on):
    rows = (await science_on.exec(select(KnowledgeBaseTable))).all()
    assert [r.slug for r in rows] == ["molten-salt-papers"]


async def test_seed_project_ids_are_stable(science_on):
    """Fixed UUIDs keep external references (docs, fixtures) stable across re-seeds."""
    alloy = await _project(science_on, "alloy-design")
    molten = await _project(science_on, "molten-salt")
    assert str(alloy.id) == "f855bdd8-c433-423e-ab5c-3a9a63b6e661"
    assert str(molten.id) == "282531e7-1e05-4369-a339-9d1b4f20aa89"


async def test_llm_pretraining_is_a_library_skill_in_no_project(seeded):
    """Seeded into the public skill library (no vista-data assets needed), but not
    attached to any default project: users opt their own project into it."""
    assert "llm-pretraining" in await _skill_names(seeded)
    for project in (await seeded.exec(select(ProjectTable))).all():
        assert "llm-pretraining" not in project.skills, project.name
