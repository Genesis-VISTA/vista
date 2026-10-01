"""The SPLASH campaign is folded into the `molten-salt` project (not a separate project)."""

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.config import settings
from vista_backend.db.schemas import ProjectTable, SkillTable
from vista_backend.db import seed as seed_module
from vista_backend.db.seed import seed_db

from test_seed_payload import make_payload, make_vector_store


@pytest.mark.anyio
async def test_seed_folds_splash_into_molten_salt(tmp_path, monkeypatch):
    # Seed from a payload (science enabled by its contents) into a throwaway data dir
    # so skill copies / storage land under tmp_path rather than the real data volume.
    # The index is prebuilt, so nothing is embedded.
    data_dir = tmp_path / "data"
    make_vector_store(data_dir / "knowledge-bases" / "molten-salt-papers" / "rag_db")
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "vista_data_token", None)
    monkeypatch.setattr(
        settings, "vista_data_payload_dir", make_payload(tmp_path / "payload")
    )
    monkeypatch.setattr(seed_module, "REPO_ROOT", tmp_path / "repo")

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
        # SPLASH is folded into molten-salt, not seeded as its own project.
        assert (
            await session.exec(
                select(ProjectTable).where(ProjectTable.name == "splash")
            )
        ).first() is None

        ms = (
            await session.exec(
                select(ProjectTable).where(ProjectTable.name == "molten-salt")
            )
        ).first()
        assert ms is not None
        # The campaign planner + both sim skills are wired into molten-salt.
        assert {"splash-planner", "salt-neutronics-tbr", "salt-chemistry-md"}.issubset(
            set(ms.skills)
        )
        # The system prompt covers the campaign, and the HPC toolchain is allowed.
        assert "SPLASH" in ms.system_prompt and "TBR" in ms.system_prompt
        assert "*" in ms.tools and "!submit_hpc_job" not in ms.tools

        # The skills the project references are registered.
        skill_names = {s.name for s in (await session.exec(select(SkillTable))).all()}
        assert {"splash-planner", "salt-neutronics-tbr", "salt-chemistry-md"}.issubset(
            skill_names
        )

    await engine.dispose()
