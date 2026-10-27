"""
`db/schema_check.py`: how a live database is compared with the models.

Each test builds a real SQLite file with `create_all`, damages it the way a
missed migration would, and reads the check's verdict.
"""

import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from vista_backend.db.schema_check import schema_problems


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "check.db"
    engine = create_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(engine)
    engine.dispose()
    connection = sqlite3.connect(path)
    yield connection
    connection.close()


def test_a_fresh_create_all_database_passes(conn):
    assert schema_problems(conn) == []
    assert schema_problems(conn, assume_additive=True) == []


def test_a_missing_not_null_column_fails_naming_it(conn):
    conn.execute("ALTER TABLE project DROP COLUMN modified")

    for additive in (False, True):
        problems = schema_problems(conn, assume_additive=additive)
        assert len(problems) == 1
        assert problems[0].startswith("project.modified")


def test_an_extra_not_null_column_without_a_default_fails_naming_it(conn):
    # With a default, inserts still work, so it passes.
    conn.execute("ALTER TABLE project ADD COLUMN legacy TEXT NOT NULL DEFAULT ''")
    assert schema_problems(conn) == []

    # SQLite cannot add NOT NULL without a default to a table, so rebuild one.
    conn.execute("DROP TABLE displayed_file")
    conn.execute(
        "CREATE TABLE displayed_file (id TEXT PRIMARY KEY, legacy TEXT NOT NULL)"
    )

    problems = schema_problems(conn, assume_additive=True)
    assert "displayed_file.legacy" in [p.split(":")[0] for p in problems]


def test_a_missing_table_fails_naming_it(conn):
    conn.execute("DROP TABLE displayed_file")

    problems = schema_problems(conn)
    assert len(problems) == 1
    assert problems[0].startswith("displayed_file")


def test_assume_additive_accepts_a_missing_table(conn):
    conn.execute("DROP TABLE displayed_file")
    assert schema_problems(conn, assume_additive=True) == []


def test_assume_additive_accepts_a_missing_nullable_column(conn):
    table, column = next(
        (t.name, c.name)
        for t in SQLModel.metadata.sorted_tables
        for c in t.columns
        if c.nullable and not c.primary_key and not c.foreign_keys and not c.index
    )
    conn.execute(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')

    assert schema_problems(conn)  # a strict check still names it
    assert schema_problems(conn, assume_additive=True) == []


def test_extra_nullable_columns_and_unknown_tables_are_ignored(conn):
    conn.execute("ALTER TABLE project ADD COLUMN scratch TEXT")
    conn.execute("CREATE TABLE not_a_model (x TEXT NOT NULL)")
    assert schema_problems(conn) == []


def test_the_allowlist_skips_a_named_quirk(conn):
    conn.execute("ALTER TABLE project DROP COLUMN modified")
    assert schema_problems(conn, allow={"project.modified"}) == []
