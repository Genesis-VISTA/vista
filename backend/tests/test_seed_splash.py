"""The seed creates a runnable `splash` project wired to the planner + sim skills (offline)."""
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
async def test_seed_creates_splash_project(tmp_path, monkeypatch):
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
        splash = (
            await session.exec(select(ProjectTable).where(ProjectTable.name == "splash"))
        ).first()
        assert splash is not None
        assert splash.system_prompt and "TBR" in splash.system_prompt
        assert {"splash-planner", "salt-neutronics-tbr", "salt-chemistry-md"}.issubset(
            set(splash.skills)
        )
        # The HPC toolchain is allowed (campaign dispatches/monitors jobs).
        assert "*" in splash.tools and "!submit_hpc_job" not in splash.tools

        # The skills the project references are registered.
        skill_names = {s.name for s in (await session.exec(select(SkillTable))).all()}
        assert {"splash-planner", "salt-neutronics-tbr", "salt-chemistry-md"}.issubset(skill_names)

    await engine.dispose()
