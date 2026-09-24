"""
`scripts/migrate_columns.py` on a database from the h5i-era branch.

Those databases have `debate_participant.box_slug` / `box_id` as NOT NULL, so
every participant insert fails until they are dropped. The script must drop
them, add what is missing, and do nothing the second time.
"""

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.db.schemas import DebateParticipantTable

SCRIPT = Path(__file__).parents[1] / "scripts" / "migrate_columns.py"

pytestmark = pytest.mark.anyio


def _script():
    spec = importlib.util.spec_from_file_location("migrate_columns", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def _branch_era_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        # Recreate the two tables the way the h5i branch had them.
        await conn.execute(text("DROP TABLE debate_participant"))
        await conn.execute(
            text(
                """CREATE TABLE debate_participant (
                    id CHAR(32) PRIMARY KEY, run_id CHAR(32) NOT NULL,
                    identity VARCHAR NOT NULL, debate_role VARCHAR NOT NULL,
                    forum_role VARCHAR NOT NULL, box_slug VARCHAR NOT NULL,
                    box_id VARCHAR NOT NULL, policy_digest VARCHAR,
                    active BOOLEAN NOT NULL, granted_tools JSON,
                    UNIQUE (run_id, identity))"""
            )
        )
        for column in ("published", "on_remote"):
            await conn.execute(text(f'ALTER TABLE debate_post DROP COLUMN "{column}"'))
        await conn.execute(text("ALTER TABLE debate_post ADD COLUMN box_id VARCHAR"))
        await conn.execute(
            text("ALTER TABLE debate_post ADD COLUMN policy_digest VARCHAR")
        )
    return engine


async def _columns(engine, table):
    async with engine.connect() as conn:
        rows = await conn.execute(text(f"PRAGMA table_info({table})"))
        return {row[1] for row in rows}


async def test_the_box_columns_are_dropped_and_inserts_work_again(tmp_path):
    engine = await _branch_era_db(tmp_path)
    migrate = _script()

    async with engine.begin() as conn:
        assert await migrate.reconcile(conn, dry_run=True) == (2, 5)
    assert "box_slug" in await _columns(engine, "debate_participant"), "dry run"

    async with engine.begin() as conn:
        assert await migrate.reconcile(conn, dry_run=False) == (2, 5)

    participant = await _columns(engine, "debate_participant")
    post = await _columns(engine, "debate_post")
    assert not {"box_slug", "box_id", "policy_digest"} & participant
    assert not {"box_id", "policy_digest"} & post
    assert {"published", "on_remote"} <= post

    async with AsyncSession(engine) as session:
        session.add(
            DebateParticipantTable(
                run_id=uuid.uuid4(),
                identity="vista-proposer-1a2b3c4d",
                debate_role="proposer",
                forum_role="proposer",
                granted_tools=[],
            )
        )
        await session.commit()

    async with engine.begin() as conn:
        assert await migrate.reconcile(conn, dry_run=False) == (0, 0), "idempotent"
    await engine.dispose()
