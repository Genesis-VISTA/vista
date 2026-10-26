"""
Compare a live SQLite database with the models, without an engine.

Startup runs this before it writes anything (inside the upgrade transaction,
before stamping an unversioned database, and before the additive step on a
current one), and the drift test in CI runs it on every upgraded fixture. It
works on a plain `sqlite3.Connection` because the first two callers have no
engine yet and the first is inside a transaction of its own.

What it refuses is what the startup column-adder cannot repair and what
breaks queries or inserts at run time:

- a model table or column the database lacks (`assume_additive` forgives a
  missing table, and a missing column the column-adder could add: nullable, or
  with a server default);
- a NOT NULL column with no default that the models do not declare, since
  every insert the code makes would fail on it.

It does not compare types, ignores column order, and ignores extra nullable
columns and tables the models do not know: those cost nothing.
"""

import sqlite3
from collections.abc import Collection

from sqlmodel import SQLModel

from . import schemas  # noqa: F401  (registers every table on the metadata)


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {name for (name,) in rows}


def schema_problems(
    conn: sqlite3.Connection,
    *,
    assume_additive: bool = False,
    allow: Collection[str] = (),
) -> list[str]:
    """
    List, as `table.column` strings, how the database cannot serve the models.

    `assume_additive` treats whatever `create_all` and the column-adder would
    add as present. `allow` names known quirks as `table.column`, skipped in
    either direction. An empty list means the database passes.
    """
    present_tables = _tables(conn)
    problems: list[str] = []
    for table in SQLModel.metadata.sorted_tables:
        if table.name not in present_tables:
            if not assume_additive:
                problems.append(f"{table.name}: table is missing")
            continue
        # One `PRAGMA table_info` row per column: (cid, name, type, notnull, default, pk).
        found = {
            row[1]: row
            for row in conn.execute(f'PRAGMA table_info("{table.name}")').fetchall()
        }
        for column in table.columns:
            label = f"{table.name}.{column.name}"
            if column.name in found or label in allow:
                continue
            addable = column.nullable or column.server_default is not None
            if assume_additive and addable:
                continue
            problems.append(f"{label}: column is missing from the database")
        for name, (_cid, _n, _type, notnull, default, pk) in found.items():
            label = f"{table.name}.{name}"
            if name in table.columns or label in allow:
                continue
            if notnull and default is None and not pk:
                problems.append(
                    f"{label}: NOT NULL with no default, and the models do not "
                    "declare it, so inserts fail"
                )
    return problems
