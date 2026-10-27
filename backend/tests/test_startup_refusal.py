"""
An incompatible database stops the backend and tells the launcher why.

`lifespan` writes the refusal's code to `VISTA_STARTUP_FAILURE_FILE` (when set)
and re-raises, which is what makes uvicorn exit non-zero.
"""

import sqlite3

import pytest

from vista_backend.api import api as api_module
from vista_backend.config import settings
from vista_backend.db import db as db_module
from vista_backend.db.db import DATABASE_INCOMPATIBLE, DatabaseIncompatible


@pytest.fixture
def incompatible_database(tmp_path, monkeypatch):
    path = tmp_path / "vista.db"
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{path}")
    db_module.get_engine.cache_clear()
    conn = sqlite3.connect(path)
    # A table the models know, lacking its NOT NULL columns.
    conn.execute("CREATE TABLE project (id CHAR(32) NOT NULL PRIMARY KEY)")
    conn.commit()
    conn.close()
    yield path
    db_module.get_engine.cache_clear()


async def _start() -> None:
    async with api_module.lifespan(api_module.app):
        pytest.fail("the backend started on a database it must refuse")


@pytest.mark.anyio
async def test_a_refusal_writes_its_code_and_stops(
    incompatible_database, tmp_path, monkeypatch
):
    failure = tmp_path / "startup-failure"
    monkeypatch.setenv("VISTA_STARTUP_FAILURE_FILE", str(failure))

    with pytest.raises(DatabaseIncompatible) as refused:
        await _start()

    assert refused.value.code == DATABASE_INCOMPATIBLE
    assert failure.read_text(encoding="utf-8") == "database-incompatible\n"


@pytest.mark.anyio
async def test_a_refusal_without_the_variable_writes_nothing_and_still_stops(
    incompatible_database, tmp_path, monkeypatch
):
    monkeypatch.delenv("VISTA_STARTUP_FAILURE_FILE", raising=False)

    with pytest.raises(DatabaseIncompatible):
        await _start()

    assert [p.name for p in tmp_path.iterdir() if "failure" in p.name] == []
