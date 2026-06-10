#!/usr/bin/env python
"""
Add a VISTA user directly to the database

Usage (from the backend/ directory):
    uv run python scripts/add_user.py --email someone@ornl.gov
    uv run python scripts/add_user.py --email admin@ornl.gov --is-admin
"""
import argparse
import sys

from sqlalchemy import create_engine
from sqlmodel import Session, select

from vista_backend.config import settings
from vista_backend.db.schemas import UserTable


def sync_database_url() -> str:
    """The backend uses an async driver; strip it for a plain sync connection."""
    return settings.database_url.replace("+aiosqlite", "").replace("+asyncpg", "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Add a VISTA user to the database.")
    parser.add_argument("--email", required=True, help="The user's email address.")
    parser.add_argument("--is-admin", action="store_true", help="Grant admin privileges.")
    args = parser.parse_args()

    engine = create_engine(sync_database_url())
    with Session(engine) as session:
        existing = session.exec(
            select(UserTable).where(UserTable.email == args.email)
        ).first()
        if existing is not None:
            print(f"User {args.email!r} already exists (is_admin={existing.is_admin}). Nothing to do.")
            return 0
        user = UserTable(email=args.email, is_admin=args.is_admin)
        session.add(user)
        session.commit()
        session.refresh(user)
        print(f"Created user {user.email!r} (id={user.id}, is_admin={user.is_admin}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
