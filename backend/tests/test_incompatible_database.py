"""
`init_db`'s compatibility check (`db/db.py`, `db/schema_check.py`): a database
the models cannot serve is refused before anything is written, with an index of
what it holds beside it.

Everything runs on real SQLite files in `tmp_path`. "Unchanged" means the bytes
of `vista.db` itself; reading it may leave `-wal`/`-shm` companions beside it,
which is fine.

Seeding is stubbed in the `start` fixture, where it is not what is under test.
`test_a_fresh_database_really_seeds` runs the real seeding.
"""

import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from vista_backend.config import settings
from vista_backend.db import db as db_module, schemas, seed as seed_module  # noqa: F401
from vista_backend.db.db import (
    DATABASE_INCOMPATIBLE,
    INDEX_FILE_NAME,
    DatabaseIncompatible,
)

FIXTURES = Path(__file__).parent / "fixtures" / "db"


def _build(path) -> None:
    """A database as `create_all` leaves it."""
    engine = create_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(engine)
    engine.dispose()


def _sql(path, *statements: str) -> None:
    conn = sqlite3.connect(path)
    for statement in statements:
        conn.execute(statement)
    conn.commit()
    conn.close()


def _columns(path, table: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
    finally:
        conn.close()


def _tables(path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {
            n
            for (n,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()


def _index(path) -> dict:
    return json.loads((path.parent / INDEX_FILE_NAME).read_text(encoding="utf-8"))


@pytest.fixture
def start(tmp_path, monkeypatch):
    """Run `init_db` on `tmp_path/vista.db`, with seeding stubbed out."""
    path = tmp_path / "vista.db"
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{path}")
    called: list[str] = []

    def stub(name):
        async def nothing(*_a, **_k) -> None:
            called.append(name)

        return nothing

    for name in ("seed_db", "sync_default_projects", "sync_bundled_skills"):
        monkeypatch.setattr(db_module, name, stub(name))
    db_module.get_engine.cache_clear()

    async def run() -> None:
        try:
            await db_module.init_db()
        finally:
            await db_module.get_engine().dispose()
            db_module.get_engine.cache_clear()

    run.path = path
    run.seeded = called
    yield run
    db_module.get_engine.cache_clear()


# --- databases that start ---------------------------------------------------


@pytest.mark.anyio
async def test_a_missing_database_starts_and_reaches_seeding(start):
    # Seeding is stubbed here: the point is that startup reaches it.
    await start()

    assert "project" in _tables(start.path)
    assert "seed_db" in start.seeded
    assert not (start.path.parent / INDEX_FILE_NAME).exists()


@pytest.mark.anyio
async def test_an_empty_database_file_starts_and_reaches_seeding(start):
    sqlite3.connect(start.path).close()

    await start()

    assert "project" in _tables(start.path)
    assert "seed_db" in start.seeded


@pytest.mark.anyio
async def test_a_fresh_database_really_seeds(tmp_path, monkeypatch):
    """The unstubbed path: real seeding fills a database that did not exist."""
    path = tmp_path / "vista.db"
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{path}")
    db_module.get_engine.cache_clear()
    try:
        await db_module.init_db()
    finally:
        await db_module.get_engine().dispose()
        db_module.get_engine.cache_clear()

    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT count(*) FROM local_settings").fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.anyio
async def test_a_missing_nullable_column_is_filled_in(start):
    _build(start.path)
    _sql(start.path, "ALTER TABLE project DROP COLUMN description")

    await start()

    assert "description" in _columns(start.path, "project")


@pytest.mark.anyio
async def test_a_missing_table_is_created(start):
    _build(start.path)
    _sql(start.path, "DROP TABLE displayed_file")

    await start()

    assert "displayed_file" in _tables(start.path)


@pytest.mark.anyio
async def test_an_extra_nullable_column_starts(start):
    _build(start.path)
    _sql(start.path, "ALTER TABLE project ADD COLUMN legacy TEXT")

    await start()

    assert "legacy" in _columns(start.path, "project")


# --- databases that are refused ---------------------------------------------


def _break(path, label: str) -> str:
    """Make a database the models cannot serve; return the column it names."""
    _build(path)
    if label == "missing":
        _sql(path, "ALTER TABLE project DROP COLUMN modified")
        return "project.modified"
    conn = sqlite3.connect(path)
    (ddl,) = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'local_settings'"
    ).fetchone()
    conn.close()
    assert "PRIMARY KEY (" in ddl
    _sql(
        path,
        "DROP TABLE local_settings",
        ddl.replace(
            "PRIMARY KEY (", "legacy_required VARCHAR NOT NULL, PRIMARY KEY (", 1
        ),
    )
    return "local_settings.legacy_required"


@pytest.mark.anyio
@pytest.mark.parametrize("label", ["missing", "unwritten"])
async def test_a_not_null_gap_is_refused_before_any_write(start, label):
    """`missing`: a model NOT NULL column absent. `unwritten`: a NOT NULL column the models lack."""
    column = _break(start.path, label)
    before = start.path.read_bytes()

    with pytest.raises(DatabaseIncompatible) as refused:
        await start()

    assert start.path.read_bytes() == before
    assert refused.value.code == DATABASE_INCOMPATIBLE == "database-incompatible"
    assert any(column in p for p in refused.value.problems)
    message = str(refused.value)
    assert refused.value.message == message
    assert column in message
    assert str(start.path) in message
    assert "Back up and reset" in message
    assert "delete vista.db" in message
    assert start.seeded == []


# --- the index --------------------------------------------------------------


@pytest.mark.anyio
async def test_the_index_lists_what_the_database_holds(start):
    _build(start.path)
    _sql(
        start.path,
        "INSERT INTO project (id, name, modified, skills, knowledge_bases, tools) "
        "VALUES ('0123456789abcdef0123456789abcdef', 'P One', 0, '[]', '[]', '[]')",
        "INSERT INTO knowledge_base (id, slug, name, pdfs_dir, rag_db_path, "
        "shared_with_mcp, build_status, modified, created_at, updated_at) VALUES "
        f"('fedcba9876543210fedcba9876543210', 'kb-one', 'KB One', "
        f"'{start.path.parent}/knowledge-bases/kb-one/pdfs', '/elsewhere/rag', 0, "
        "'ready', 0, '2026-01-01', '2026-01-01')",
        "INSERT INTO skill (id, name, description, path, bundled, modified, "
        "created_at, updated_at) VALUES ('00000000000040008000000000000009', "
        "'sk', 'd', 'storage/abc', 0, 0, '2026-01-01', '2026-01-01')",
        "ALTER TABLE project DROP COLUMN modified",
    )
    before = start.path.read_bytes()

    with pytest.raises(DatabaseIncompatible):
        await start()

    assert start.path.read_bytes() == before
    assert _index(start.path) == {
        "database": "vista.db",
        "projects": [
            {
                "id": "01234567-89ab-cdef-0123-456789abcdef",
                "name": "P One",
                "files": "volumes/01234567-89ab-cdef-0123-456789abcdef",
                "comes_with_vista": False,
            }
        ],
        "skills": [{"name": "sk", "path": "storage/abc", "comes_with_vista": False}],
        "knowledge_bases": [
            {
                "slug": "kb-one",
                "name": "KB One",
                "pdfs": "knowledge-bases/kb-one/pdfs",
                "comes_with_vista": False,
            }
        ],
    }


@pytest.mark.anyio
async def test_a_database_without_a_skill_table_still_gets_an_index(start):
    _build(start.path)
    _sql(
        start.path,
        "INSERT INTO project (id, name, modified, skills, knowledge_bases, tools) "
        "VALUES ('0123456789abcdef0123456789abcdef', 'P One', 0, '[]', '[]', '[]')",
        "DROP TABLE skill",
        "ALTER TABLE project DROP COLUMN modified",
    )
    before = start.path.read_bytes()

    with pytest.raises(DatabaseIncompatible):
        await start()

    assert start.path.read_bytes() == before
    index = _index(start.path)
    assert [p["name"] for p in index["projects"]] == ["P One"]
    assert index["skills"] == []
    assert index["knowledge_bases"] == []


@pytest.mark.anyio
async def test_an_unwritable_index_does_not_hide_the_refusal(start, monkeypatch):
    _break(start.path, "missing")

    def deny(self, *_a, **_k):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "write_text", deny)

    with pytest.raises(DatabaseIncompatible):
        await start()


@pytest.mark.anyio
async def test_a_passing_database_gets_no_index(start):
    _build(start.path)

    await start()

    assert not (start.path.parent / INDEX_FILE_NAME).exists()


# --- a real v0.1.4 database -------------------------------------------------


@pytest.mark.anyio
async def test_a_v0_1_4_database_is_refused_and_indexed(start):
    # v0.1.4.sql is a dump of a real v0.1.4 database, copied from the unmerged
    # `db-migrations` branch (commit 4caf8a99, backend/tests/fixtures/db/).
    # Paths in it use the token __VISTA_DATA_DIR__, replaced by the data dir.
    dump = (FIXTURES / "v0.1.4.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(start.path)
    conn.executescript(dump.replace("__VISTA_DATA_DIR__", str(start.path.parent)))
    conn.close()
    before = start.path.read_bytes()

    with pytest.raises(DatabaseIncompatible) as refused:
        await start()

    assert start.path.read_bytes() == before
    message = str(refused.value)
    assert "chat_session.user_id" in message
    assert "skill.is_public" in message
    index = _index(start.path)
    assert {p["name"] for p in index["projects"]} == {
        "alloy-design",
        "molten-salt",
        "ai-safety-autonomous-labs",
        "fixture-project",
    }
    assert len(index["skills"]) == 13
    assert {k["slug"] for k in index["knowledge_bases"]} == {
        "molten-salt-papers",
        "ai-safety",
        "fixture-kb",
    }
    for kb in index["knowledge_bases"]:
        assert kb["pdfs"] == f"knowledge-bases/{kb['slug']}/pdfs"
    assert {k["slug"]: k["comes_with_vista"] for k in index["knowledge_bases"]} == {
        "molten-salt-papers": True,
        "ai-safety": True,
        "fixture-kb": False,
    }
    assert {p["name"]: p["comes_with_vista"] for p in index["projects"]} == {
        "alloy-design": True,
        "molten-salt": True,
        "ai-safety-autonomous-labs": True,
        "fixture-project": False,
    }
    bundled = {d.name for d in seed_module._bundled_skill_dirs()}
    assert all(s["comes_with_vista"] == (s["name"] in bundled) for s in index["skills"])
    # the fixture's 13 skills are all bundled today, with no researcher's own
    assert sum(s["comes_with_vista"] for s in index["skills"]) == 13
