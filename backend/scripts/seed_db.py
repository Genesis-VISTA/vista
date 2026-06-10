#!/usr/bin/env python
"""
Seed the VISTA database with default data.

Idempotent: if the database has already been seeded this is a no-op

Usage (from the backend/ directory):
    uv run python scripts/seed_db.py
"""
import asyncio
import sys

from vista_backend.config import settings # import so logging gets configured
from vista_backend.db.db import init_db
from vista_backend.utils import indexer

async def main() -> int:
    try:
        await init_db()
    finally:
        # Shutdown so script exists properly
        indexer.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
