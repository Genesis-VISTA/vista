"""
Bring the debate tables' columns in line with the models.

The app creates tables with `SQLModel.metadata.create_all`, which adds missing
*tables* and never missing *columns*. So a deployment that ran an earlier version
keeps its `debate_post` exactly as it was, and the first query naming a new
column fails with "no such column" — at read time, in the API, rather than at
startup where it would be obvious.

Three steps, all idempotent:

  - **Add** any column a table lacks (`ADD COLUMN`).
  - **Drop** the columns the h5i-era forum wrote and nothing reads any more
    (`RETIRED`). They matter because `debate_participant.box_slug` and `box_id`
    were NOT NULL: on a branch tester's database every new participant insert
    fails until they are gone. Startup never drops anything, so this is the only
    place it happens.
  - **Move** the forum outbox out of this database. An early build of the git
    forum kept it in a `forum_outbox` table; it now lives in each project's
    `data/forum-git/<project-id>/outbox.db`. Rows are copied there (so posts
    this install wrote keep reading as host-observed) and the table is dropped.

Safe to run repeatedly, and safe to run on a database that is already current.

    uv run python scripts/migrate_columns.py [--dry-run]

SQLite only (DROP COLUMN needs SQLite 3.35+), which is what `database_url`
defaults to. On Postgres, use a real migration tool.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from vista_backend.config import settings
from vista_backend.db.db import get_engine
from vista_backend.services.forum_git import OUTBOX_FILE, FileOutbox
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

RETIRED: dict[str, tuple[str, ...]] = {
    "debate_participant": ("box_slug", "box_id", "policy_digest"),
    "debate_post": ("box_id", "policy_digest"),
}
"""Columns from the h5i forum's per-role boxes, removed with it."""


async def reconcile(conn: AsyncConnection, *, dry_run: bool) -> tuple[int, int]:
    """Add missing columns and drop retired ones. Returns (added, dropped)."""
    added = dropped = 0
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
            if dry_run:
                print(f"would run: {clause}")
            else:
                await conn.execute(text(clause))
                print(f"added {table_name}.{column.name}")
            added += 1

        for name in RETIRED.get(table_name, ()):
            if name not in present or name in table.columns:
                continue
            clause = f"ALTER TABLE {table_name} DROP COLUMN {name}"
            if dry_run:
                print(f"would run: {clause}")
            else:
                await conn.execute(text(clause))
                print(f"dropped {table_name}.{name}")
            dropped += 1
    return added, dropped


async def move_outbox(conn: AsyncConnection, *, dry_run: bool) -> int:
    """Copy `forum_outbox` rows into per-project outbox files, then drop it."""
    exists = (
        await conn.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='forum_outbox'"
            )
        )
    ).first()
    if exists is None:
        return 0
    rows = (
        await conn.execute(
            text(
                "SELECT project_id, post_id, thread_id, created_at, published_at "
                "FROM forum_outbox"
            )
        )
    ).all()
    by_project: dict[str, list[tuple[str, str, str, str | None]]] = defaultdict(list)
    for project_id, post_id, thread_id, created_at, published_at in rows:
        by_project[str(uuid.UUID(str(project_id)))].append(
            (post_id, thread_id, created_at, published_at)
        )
    for project_id, project_rows in sorted(by_project.items()):
        target = settings.forum_git_dir / project_id / OUTBOX_FILE
        if dry_run:
            print(f"would copy {len(project_rows)} outbox row(s) to {target}")
        else:
            added = FileOutbox(target).insert_rows(project_rows)
            print(f"copied {added} outbox row(s) to {target}")
    if dry_run:
        print("would run: DROP TABLE forum_outbox")
    else:
        await conn.execute(text("DROP TABLE forum_outbox"))
        print("dropped forum_outbox")
    return len(rows)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without altering anything",
    )
    args = parser.parse_args()

    if not settings.database_url.startswith("sqlite"):
        print(f"refusing: {settings.database_url.split('://')[0]} is not sqlite")
        return 2

    async with get_engine().begin() as conn:
        added, dropped = await reconcile(conn, dry_run=args.dry_run)
        moved = await move_outbox(conn, dry_run=args.dry_run)

    changed = added + dropped + moved
    if changed == 0:
        print("nothing to do — the tables are current")
    elif args.dry_run:
        print(
            f"{added} column(s) to add, {dropped} to drop, {moved} outbox row(s) "
            "to move; re-run without --dry-run to apply"
        )
    else:
        print(
            f"{added} column(s) added, {dropped} dropped, {moved} outbox row(s) moved"
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
