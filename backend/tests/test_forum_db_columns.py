"""
The forum's database additions reach both new and existing databases.

`init_db` builds the schema with `create_all` then `_add_missing_columns`; these
tests run exactly that pair on a SQLite file. A new table (`forum_outbox`) must
appear in either case, and the new nullable post columns must be added to a
`debate_post` table created before they existed — a branch tester's database.
"""

from sqlalchemy import create_engine, inspect, text
from sqlmodel import SQLModel

from vista_backend.db.db import _add_missing_columns
from vista_backend.db import schemas  # noqa: F401  (registers the tables)

NEW_POST_COLUMNS = {"published", "on_remote"}
OUTBOX_COLUMNS = {"post_id", "project_id", "thread_id", "created_at", "published_at"}


def _build(engine) -> None:
    with engine.begin() as conn:
        SQLModel.metadata.create_all(conn)
        _add_missing_columns(conn)


def _columns(engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_a_fresh_database_gets_the_outbox_and_post_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    _build(engine)
    assert OUTBOX_COLUMNS <= _columns(engine, "forum_outbox")
    assert NEW_POST_COLUMNS <= _columns(engine, "debate_post")
    pk = inspect(engine).get_pk_constraint("forum_outbox")["constrained_columns"]
    assert pk == ["post_id"]


def test_an_existing_database_gains_them_at_startup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    _build(engine)
    # Make it look like a database from before this change.
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE forum_outbox"))
        for column in NEW_POST_COLUMNS:
            conn.execute(text(f'ALTER TABLE debate_post DROP COLUMN "{column}"'))
    assert not NEW_POST_COLUMNS & _columns(engine, "debate_post")

    _build(engine)

    assert OUTBOX_COLUMNS <= _columns(engine, "forum_outbox")
    assert NEW_POST_COLUMNS <= _columns(engine, "debate_post")
