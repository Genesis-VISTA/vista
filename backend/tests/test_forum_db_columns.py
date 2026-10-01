"""
The forum's database additions reach both new and existing databases.

`init_db` builds the schema with `create_all` then `_add_missing_columns`; these
tests run exactly that pair on a SQLite file. The new nullable columns must be
there on a fresh database and be added to tables created before they existed —
a branch tester's database.

The forum's outbox is deliberately *not* in this database (it lives beside each
project's repository, `services/forum_git.FileOutbox`), and this pins that too.
"""

from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

from vista_backend.db.db import _add_missing_columns
from vista_backend.db import schemas  # noqa: F401  (registers the tables)

NEW_COLUMNS = {
    "debate_post": {"published", "on_remote"},
    "debate_run": {"thread_missing"},
}


def _build(engine) -> None:
    with engine.begin() as conn:
        SQLModel.metadata.create_all(conn)
        _add_missing_columns(conn)


def _columns(engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_a_fresh_database_gets_the_new_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    _build(engine)
    for table, columns in NEW_COLUMNS.items():
        assert columns <= _columns(engine, table)
    assert "forum_outbox" not in inspect(engine).get_table_names()


def test_an_existing_database_gains_them_at_startup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    _build(engine)
    # Make it look like a database from before this change.
    with engine.begin() as conn:
        for table, columns in NEW_COLUMNS.items():
            for column in columns:
                conn.execute(text(f'ALTER TABLE {table} DROP COLUMN "{column}"'))

    _build(engine)

    for table, columns in NEW_COLUMNS.items():
        assert columns <= _columns(engine, table)


def test_chat_session_run_columns_reach_an_existing_database(tmp_path):
    """
    `run_state` and `run_unseen` are NOT NULL, which `_add_missing_columns` only
    adds when they carry a server default. Existing rows must read back idle.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    _build(engine)
    run_columns = {"run_state", "run_unseen", "run_events"}
    with engine.begin() as conn:
        for column in run_columns:
            conn.execute(text(f'ALTER TABLE chat_session DROP COLUMN "{column}"'))
    assert not run_columns & _columns(engine, "chat_session")

    _build(engine)

    assert run_columns <= _columns(engine, "chat_session")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chat_session (id, user_id, project_id, title, "
                "message_history, messages, created_at, updated_at) VALUES "
                "('a', 'u', 'p', 't', '[]', '[]', '', '')"
            )
        )
        row = conn.execute(
            text("SELECT run_state, run_unseen, run_events FROM chat_session")
        ).one()
    assert tuple(row) == ("idle", 0, None)
