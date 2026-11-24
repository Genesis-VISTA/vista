import asyncio
import os
import re
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import settings
from ..db.ownership import visible, uid
from ..db.schemas import (
    LOCAL_USER_ID,
    KnowledgeBaseCreate,
    KnowledgeBaseTable,
    KnowledgeBaseUpdate,
    Publication,
    UserTable,
    is_valid_slug,
)
from ..utils import indexer
from .teams import set_team, team_for_new


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


def kb_folder(user_id: str | None, slug: str) -> Path:
    """
    The one place a knowledge base's directory is decided, from its owner and
    slug: `<knowledge_bases_dir>/<slug>` for `local` (nothing moves on desktop)
    and for a global one (`user_id` None, the docs corpus);
    `<knowledge_bases_dir>/<user_id>/<slug>` for any other user. The MCP server
    resolves the same layout (`rag_mcp._kb_dir`).
    """
    if user_id is None or user_id == LOCAL_USER_ID:
        return settings.knowledge_bases_dir / slug
    return settings.knowledge_bases_dir / user_id / slug


def kb_dir_of(kb: KnowledgeBaseTable) -> Path:
    """`kb_folder` for a row."""
    return kb_folder(kb.user_id, kb.slug)


_locks: dict[str, asyncio.Lock] = {}


def kb_lock(user_id: str | None, slug: str) -> asyncio.Lock:
    """
    The lock a publish or import of the knowledge base with `slug` holds from
    its checks to its commit, and an import until the store it replaced is
    removed: two interleaved, an import's `CURRENT` and row could name
    different stores, and each would remove the other's.
    """
    return _locks.setdefault(str(kb_folder(user_id, slug)), asyncio.Lock())


@asynccontextmanager
async def kb_writing(user_id: str | None, slug: str, doing: str) -> AsyncIterator[None]:
    """
    Hold the slug's lock (`kb_lock`) for any other change to the knowledge
    base's row or files, through its commit, so a publish or import cannot
    interleave with it: an import commits a new store and publications, which
    a change read before it and committed after would write back over.
    Refused (409) while a publish or import holds it, rather than waited for,
    as those take minutes. `doing` is a verb phrase ("delete it").
    """
    lock = kb_lock(user_id, slug)
    # Nothing is awaited between the check and taking the lock, which an
    # unheld asyncio.Lock gives at once: no publish or import can come between.
    if lock.locked():
        raise HTTPException(
            status_code=409,
            detail=f"{slug} is being published or imported; {doing} once that "
            "has finished.",
        )
    async with lock:
        yield


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


async def list_kbs(session: AsyncSession, user: UserTable) -> list[KnowledgeBaseTable]:
    """`user`'s knowledge bases and the global one (the OLCF docs corpus)."""
    return list((await session.exec(visible(select(KnowledgeBaseTable), user))).all())


async def get_kb_optional(
    session: AsyncSession, slug: str, user: UserTable
) -> KnowledgeBaseTable | None:
    """The knowledge base `user` means by `slug`: their own, else a global one."""
    stmt = visible(
        select(KnowledgeBaseTable).where(KnowledgeBaseTable.slug == slug), user
    ).order_by(col(KnowledgeBaseTable.user_id).is_(None))
    return (await session.exec(stmt)).first()


async def get_kb(
    session: AsyncSession, slug: str, user: UserTable
) -> KnowledgeBaseTable:
    kb = await get_kb_optional(session, slug, user)
    if kb is None:
        raise HTTPException(status_code=404, detail=f"Knowledge base not found: {slug}")
    return kb


async def slug_taken(session: AsyncSession, slug: str, user: UserTable) -> bool:
    """
    Whether `user` already has a knowledge base called `slug`. Stores are keyed
    on disk by owner and slug (`kb_folder`), so another user's slug is free.
    """
    row = (
        await session.exec(
            select(KnowledgeBaseTable.id).where(
                KnowledgeBaseTable.slug == slug,
                KnowledgeBaseTable.user_id == uid(user),
            )
        )
    ).first()
    return row is not None


_UUID_SLUG = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def check_slug_free_of_user_ids(slug: str) -> None:
    """400 for a uuid-shaped slug: it could be a user's folder under `knowledge_bases_dir`."""
    if _UUID_SLUG.match(slug):
        raise HTTPException(
            status_code=400,
            detail="A knowledge base's slug cannot look like an id (uuid); "
            "choose a name with some words in it.",
        )


def require_writable(kb: KnowledgeBaseTable) -> KnowledgeBaseTable:
    """409 for a global knowledge base (the OLCF docs corpus): readable by all, writable by none."""
    if kb.user_id is None:
        raise HTTPException(
            status_code=409,
            detail=f"{kb.slug} comes with VISTA and is read-only. "
            "Make your own knowledge base to add papers of your own.",
        )
    return kb


async def get_kb_writable(
    session: AsyncSession, slug: str, user: UserTable
) -> KnowledgeBaseTable:
    """`get_kb`, refusing a global one."""
    return require_writable(await get_kb(session, slug, user))


async def create_kb(
    session: AsyncSession, payload: KnowledgeBaseCreate, user: UserTable
) -> KnowledgeBaseTable:
    if not is_valid_slug(payload.slug):
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid slug. Slugs must be lowercase letters / digits / dashes, "
                "1-80 chars, must start and end with an alphanumeric character."
            ),
        )
    check_slug_free_of_user_ids(payload.slug)
    if await slug_taken(session, payload.slug, user):
        raise HTTPException(
            status_code=409, detail=f"Slug already in use: {payload.slug}"
        )
    if kb_lock(uid(user), payload.slug).locked():
        raise HTTPException(
            status_code=409,
            detail=f"A knowledge base is being imported as {payload.slug}.",
        )

    kb_dir = kb_folder(uid(user), payload.slug)
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
        user_id=uid(user),
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
    project = None
    if payload.project is not None:
        # Imported here: `project` reaches the agents, which reach this module.
        from .project import get_project_by_name

        project = await get_project_by_name(session, payload.project, user)
    set_team(row, *await team_for_new(session, project))
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def update_kb(
    session: AsyncSession, slug: str, updates: KnowledgeBaseUpdate, user: UserTable
) -> KnowledgeBaseTable:
    kb = await get_kb_writable(session, slug, user)
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


async def delete_kb(
    session: AsyncSession, slug: str, user: UserTable
) -> tuple[str, str]:
    """
    Deletes the KB row and clears in-memory indexer state.
    Returns (pdfs_dir, rag_db_path) for the caller to clean up on disk.
    Raises 404 if not found, and 409 for a global one.
    """
    kb = await get_kb_writable(session, slug, user)

    pdfs_dir = str(Path(kb.pdfs_dir).resolve())
    rag_db_path = str(Path(kb.rag_db_path).resolve())

    await session.delete(kb)

    indexer.clear_progress(rag_db_path)
    indexer.invalidate_rag_cache(rag_db_path)

    return pdfs_dir, rag_db_path
