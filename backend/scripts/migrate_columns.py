"""
Add any columns the live database is missing.

The app creates tables with `SQLModel.metadata.create_all`, which adds missing
*tables* and never missing *columns*. So a deployment that ran an earlier version
keeps its `debate_post` exactly as it was, and the first query naming a new
column fails with "no such column" — at read time, in the API, rather than at
startup where it would be obvious.

This reconciles those tables against the models. It is additive and
idempotent: it only ever issues `ADD COLUMN` for a column the table lacks, never
drops, renames, or backfills. Safe to run repeatedly, and safe to run on a
database that is already current.

    uv run python scripts/migrate_columns.py [--dry-run]

SQLite only, which is what `database_url` defaults to. On Postgres, use a real
migration tool.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text

from vista_backend.config import settings
from vista_backend.db.db import get_engine
from vista_backend.db.schemas import (  # noqa: F401 — imported to register metadata
    DebateParticipantTable,
    DebatePostTable,
    DebateRunTable,
    ProjectTable,
)
from sqlmodel import SQLModel


TABLES = ("project", "debate_run", "debate_participant", "debate_post")
"""
Tables whose columns this reconciles.

`project` joined the list when the Hypothesis Lab's repository moved onto the
project: a deployment upgrading across that change has a `project` table with no
`forum_repo_url`, and every project read would fail with "no such column" at the
first request rather than at startup.
"""


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what is missing without altering anything",
    )
    args = parser.parse_args()

    if not settings.database_url.startswith("sqlite"):
        print(f"refusing: {settings.database_url.split('://')[0]} is not sqlite")
        return 2

    engine = get_engine()
    added = 0
    async with engine.begin() as conn:
        for table_name in TABLES:
            table = SQLModel.metadata.tables.get(table_name)
            if table is None:
                print(f"skip {table_name}: not in the models")
                continue

            rows = await conn.execute(text(f"PRAGMA table_info({table_name})"))
            present = {row[1] for row in rows}
            if not present:
                print(f"skip {table_name}: not in the database (create_all will make it)")
                continue

            for column in table.columns:
                if column.name in present:
                    continue
                type_sql = column.type.compile(dialect=conn.dialect)
                clause = f"ALTER TABLE {table_name} ADD COLUMN {column.name} {type_sql}"
                if args.dry_run:
                    print(f"would run: {clause}")
                else:
                    await conn.execute(text(clause))
                    print(f"added {table_name}.{column.name}")
                added += 1

    if added == 0:
        print("nothing to do — the tables are current")
    elif args.dry_run:
        print(f"{added} column(s) missing; re-run without --dry-run to add them")
    else:
        print(f"{added} column(s) added")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
