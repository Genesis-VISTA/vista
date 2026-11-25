"""
A desktop install upgrading to the multi-user code (openspec change
`multi-user-hosted`, task 14.2, the part that runs in CI; the packaged build's
smoke test is hands-on).

A `vista.db` as an installation made before the change left it: no `app_user`
or `user_settings` table, no `user_id` column anywhere, and the researcher's
projects, chats, skills, knowledge bases and settings in it. `init_db` must
take it without `DatabaseIncompatible` (which is the reset prompt), and
everything must be `local`'s afterwards, with ids and values unchanged.
"""

import sqlite3
import uuid

import pytest
from sqlalchemy import MetaData, Table
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateTable
from sqlmodel import SQLModel

from vista_backend.config import settings
from vista_backend.db import db as db_module
from vista_backend.db.schemas import LOCAL_USER_ID

pytestmark = pytest.mark.anyio

PROJECT, CHAT, SKILL, KB = (uuid.uuid4().hex for _ in range(4))


def _pre_change_database(path) -> None:
    """The models' tables without `user_id`, and without `app_user` and `user_settings`."""
    meta = MetaData()
    for table in SQLModel.metadata.sorted_tables:
        if table.name in ("app_user", "user_settings"):
            continue
        Table(
            table.name,
            meta,
            *[c._copy() for c in table.columns if c.name != "user_id"],
        )
    conn = sqlite3.connect(path)
    try:
        for table in meta.sorted_tables:
            conn.execute(str(CreateTable(table).compile(dialect=sqlite.dialect())))
        conn.execute(
            "INSERT INTO local_settings (id, install_id, display_name, research_role,"
            " inference_provider, collaboration_server_url) VALUES (1, '%s', 'Ada', 'chemist', 'olcf', 'https://collab.example')"
            % uuid.uuid4().hex
        )
        conn.execute(
            "INSERT INTO project (id, name, skills, knowledge_bases, tools, usage_limits, modified)"
            " VALUES (?, 'my-project', '[]', '[]', '[]', '{}', 1)",
            (PROJECT,),
        )
        conn.execute(
            "INSERT INTO chat_session (id, project_id, title, created_at, updated_at)"
            " VALUES (?, ?, 'a chat', '2026-10-01', '2026-10-02')",
            (CHAT, PROJECT),
        )
        conn.execute(
            "INSERT INTO skill (id, name, description, path, bundled, modified, created_at, updated_at)"
            " VALUES (?, 'my-skill', 'd', 'p', 0, 1, '', '')",
            (SKILL,),
        )
        conn.execute(
            "INSERT INTO knowledge_base (id, slug, name, pdfs_dir, rag_db_path, publications,"
            " build_status, modified, created_at, updated_at, shared_with_mcp)"
            " VALUES (?, 'my-kb', 'my-kb', '', '', '[]', 'ready', 0, '', '', 0)",
            (KB,),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def start(tmp_path, monkeypatch):
    """Runs `init_db` on a pre-change `vista.db`; the seeders are off, as in `test_settings_split`."""
    path = tmp_path / "vista.db"
    _pre_change_database(path)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{path}")

    async def nothing(*_a, **_k) -> None:
        return None

    for name in ("seed_db", "sync_default_projects", "sync_bundled_skills"):
        monkeypatch.setattr(db_module, name, nothing)
    db_module.get_engine.cache_clear()

    async def run() -> None:
        try:
            await db_module.init_db()
        finally:
            await db_module.get_engine().dispose()
            db_module.get_engine.cache_clear()

    run.path = path
    yield run
    db_module.get_engine.cache_clear()


def _rows(path, sql: str) -> list[tuple]:
    with sqlite3.connect(path) as conn:
        return conn.execute(sql).fetchall()


async def test_a_pre_change_database_upgrades_with_everything_the_researchers(start):
    # `init_db` raises `DatabaseIncompatible`, the reset prompt, if the check fails.
    await start()

    for table, row_id in (
        ("project", PROJECT),
        ("chat_session", CHAT),
        ("skill", SKILL),
        ("knowledge_base", KB),
    ):
        assert _rows(start.path, f"SELECT id, user_id FROM {table}") == [
            (row_id, LOCAL_USER_ID)
        ], table
    assert _rows(start.path, "SELECT id FROM app_user") == [(LOCAL_USER_ID,)]
    assert _rows(start.path, "SELECT name FROM project") == [("my-project",)]

    # Settings: the researcher's fields were copied to `local`'s user row, and the
    # server row still has its own.
    assert _rows(
        start.path,
        "SELECT display_name, research_role, inference_provider FROM user_settings"
        " WHERE user_id = 'local'",
    ) == [("Ada", "chemist", "olcf")]
    assert _rows(start.path, "SELECT collaboration_server_url FROM local_settings") == [
        ("https://collab.example",)
    ]


async def test_a_second_start_changes_nothing(start):
    await start()
    first = _rows(start.path, "SELECT id, user_id FROM project")
    await start()
    assert _rows(start.path, "SELECT id, user_id FROM project") == first
    assert _rows(start.path, "SELECT count(*) FROM app_user") == [(1,)]
    assert _rows(start.path, "SELECT count(*) FROM user_settings") == [(1,)]
