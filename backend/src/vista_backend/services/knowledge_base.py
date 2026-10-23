import asyncio
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import settings
from ..db.schemas import (
    KnowledgeBaseCreate,
    KnowledgeBaseTable,
    KnowledgeBaseUpdate,
    Publication,
    is_valid_slug,
)
from ..utils import indexer


CURRENT_STORE_FILE = "CURRENT"
"""
In a knowledge base's folder, the file naming the subfolder that holds its
current PDFs and store (`<name>/pdfs`, `<name>/rag_db`), for a knowledge base
an import wrote. The MCP server has no database, so it finds a knowledge base's
store on disk; an import lands in a new subfolder and replaces this file to
switch to it, never writing over the store the MCP server has open. Kept in
lockstep with `vista_mcp_server.rag_mcp._CURRENT_STORE_FILENAME`.
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def kb_folder(slug: str) -> Path:
    """A knowledge base's own folder: `<knowledge_bases_dir>/<slug>`."""
    return settings.knowledge_bases_dir / slug


_locks: dict[str, asyncio.Lock] = {}


def kb_lock(slug: str) -> asyncio.Lock:
    """
    The lock a publish or import of the knowledge base with `slug` holds from
    its checks to its commit, and an import until the store it replaced is
    removed: two interleaved, an import's `CURRENT` and row could name
    different stores, and each would remove the other's.
    """
    return _locks.setdefault(slug, asyncio.Lock())


def read_current(kb_dir: Path) -> str | None:
    """The subfolder `CURRENT` names, or None without one."""
    try:
        return (kb_dir / CURRENT_STORE_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def write_current(kb_dir: Path, name: str | None) -> None:
    """Point `CURRENT` at `name`, in one step; None removes it."""
    pointer = kb_dir / CURRENT_STORE_FILE
    if name is None:
        pointer.unlink(missing_ok=True)
        return
    tmp = kb_dir / f".{CURRENT_STORE_FILE}.tmp"
    tmp.write_text(name, encoding="utf-8")
    os.replace(tmp, pointer)


def kb_content(kb: KnowledgeBaseTable) -> tuple[Any, ...]:
    """
    What `modified` tracks: the name, the description and which PDFs it
    holds. Not `updated_at`, which reading a knowledge base also moves (the
    reconciler records what it finds on disk and in the index).
    """
    files = sorted(
        (p.filename, p.size)
        for p in (
            Publication.model_validate(p) if isinstance(p, dict) else p
            for p in kb.publications
        )
    )
    return kb.name, kb.description, tuple(files)


async def list_kbs(session: AsyncSession) -> list[KnowledgeBaseTable]:
    return list((await session.exec(select(KnowledgeBaseTable))).all())


async def get_kb_optional(
    session: AsyncSession, slug: str
) -> KnowledgeBaseTable | None:
    return (
        await session.exec(
            select(KnowledgeBaseTable).where(KnowledgeBaseTable.slug == slug)
        )
    ).first()


async def get_kb(session: AsyncSession, slug: str) -> KnowledgeBaseTable:
    kb = await get_kb_optional(session, slug)
    if kb is None:
        raise HTTPException(status_code=404, detail=f"Knowledge base not found: {slug}")
    return kb


async def create_kb(
    session: AsyncSession, payload: KnowledgeBaseCreate
) -> KnowledgeBaseTable:
    if not is_valid_slug(payload.slug):
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid slug. Slugs must be lowercase letters / digits / dashes, "
                "1-80 chars, must start and end with an alphanumeric character."
            ),
        )
    existing = await get_kb_optional(session, payload.slug)
    if existing is not None:
        raise HTTPException(
            status_code=409, detail=f"Slug already in use: {payload.slug}"
        )
    if kb_lock(payload.slug).locked():
        raise HTTPException(
            status_code=409,
            detail=f"A knowledge base is being imported as {payload.slug}.",
        )

    kb_dir = settings.knowledge_bases_dir / payload.slug
    pdfs_dir = kb_dir / "pdfs"
    rag_db_path = kb_dir / "rag_db"
    # What a deletion could not remove (on Windows, a store the MCP server
    # held open) must not be served as this new knowledge base: the MCP server
    # prefers the store `CURRENT` names.
    write_current(kb_dir, None)
    for leftover in kb_dir.glob("store-*"):
        shutil.rmtree(leftover, ignore_errors=True)
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    rag_db_path.mkdir(parents=True, exist_ok=True)

    now = _now_iso()
    row = KnowledgeBaseTable(
        slug=payload.slug,
        name=payload.name,
        description=payload.description,
        pdfs_dir=str(pdfs_dir),
        rag_db_path=str(rag_db_path),
        shared_with_mcp=False,
        publications=[],
        build_status="pending",
        last_built_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def update_kb(
    session: AsyncSession, slug: str, updates: KnowledgeBaseUpdate
) -> KnowledgeBaseTable:
    kb = await get_kb(session, slug)
    data = updates.model_dump(exclude_unset=True)
    if any(getattr(kb, key) != value for key, value in data.items()):
        kb.modified = True
    for key, value in data.items():
        setattr(kb, key, value)
    kb.updated_at = _now_iso()
    session.add(kb)
    await session.flush()
    await session.refresh(kb)
    return kb


async def delete_kb(session: AsyncSession, slug: str) -> tuple[str, str]:
    """
    Deletes the KB row and clears in-memory indexer state.
    Returns (pdfs_dir, rag_db_path) for the caller to clean up on disk.
    Raises 404 if not found.
    """
    kb = await get_kb(session, slug)

    pdfs_dir = str(Path(kb.pdfs_dir).resolve())
    rag_db_path = str(Path(kb.rag_db_path).resolve())

    await session.delete(kb)

    indexer.clear_progress(rag_db_path)
    indexer.invalidate_rag_cache(rag_db_path)

    return pdfs_dir, rag_db_path
