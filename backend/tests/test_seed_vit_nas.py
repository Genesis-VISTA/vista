"""The seed creates a runnable `vit-nas` project wired to the planner + vit-train skills (offline)."""

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.config import settings
from vista_backend.db.schemas import ProjectTable, SkillTable
from vista_backend.db.seed import seed_db


@pytest.mark.anyio
async def test_seed_creates_vit_nas_project(tmp_path, monkeypatch):
    # Seed offline (no data token -> no downloads) and into a throwaway data dir so the
    # skill copies / storage land under tmp_path rather than the real data volume.
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
        vit_nas = (
            await session.exec(
                select(ProjectTable).where(ProjectTable.name == "vit-nas")
            )
        ).first()
        assert vit_nas is not None
        assert vit_nas.system_prompt and "efficiency" in vit_nas.system_prompt
        assert {"vit-nas-planner", "vit-train"}.issubset(set(vit_nas.skills))
        # The HPC toolchain is allowed (campaign dispatches/monitors training jobs).
        assert "*" in vit_nas.tools and "!submit_hpc_job" not in vit_nas.tools

        # The skills the project references are registered.
        skill_names = {s.name for s in (await session.exec(select(SkillTable))).all()}
        assert {"vit-nas-planner", "vit-train"}.issubset(skill_names)

    await engine.dispose()
