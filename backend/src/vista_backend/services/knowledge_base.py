from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import settings
from ..db.schemas import (
    KnowledgeBaseCreate,
    KnowledgeBaseTable,
    KnowledgeBaseUpdate,
    is_valid_slug,
)
from ..utils import indexer


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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

    kb_dir = settings.knowledge_bases_dir / payload.slug
    pdfs_dir = kb_dir / "pdfs"
    rag_db_path = kb_dir / "rag_db"
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

    indexer._clear_progress(rag_db_path)
    indexer.invalidate_rag_cache(rag_db_path)

    return pdfs_dir, rag_db_path
