"""
Uploading a PDF to a knowledge base ends with the knowledge base marked ready.

The upload schedules the indexer as a background task, and FastAPI runs
background tasks before the request's session dependency exits. So unless the
upload commits before scheduling, the indexer re-reads a row without the new
publications, records nothing, and the knowledge base stays "pending" ("Not yet
built") although its PDF is indexed.

These tests use the real per-request sessions on a file database, as in
production. The shared-session `api_client` harness would hide the bug: there
every session sees every other's uncommitted rows.
"""

import httpx
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.config import settings
from vista_backend.db.schemas import KnowledgeBaseTable

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture
async def kb_client(engine, session, alice, tmp_path, monkeypatch):
    from vista_backend.api import knowledge_bases as kb_api
    from vista_backend.api.api import app
    from vista_backend.db.db import get_engine
    from vista_backend.services.auth import get_user

    # `alice` is flushed but not committed, which holds the write lock.
    await session.commit()

    async def _user():
        return alice

    async def _index_publications(*, rag_db_path, pdfs_dir, filenames, **_):
        kb_api.indexer._clear_progress(rag_db_path)
        return [
            {
                "filename": f,
                "status": "indexed",
                "error": None,
                "chunk_count": 1,
                "citation": None,
                "citation_status": "disabled",
            }
            for f in filenames
        ]

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(kb_api, "get_engine", lambda: engine)
    monkeypatch.setattr(kb_api.indexer, "index_publications", _index_publications)
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_user] = _user
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def test_upload_marks_knowledge_base_ready(kb_client, engine):
    created = await kb_client.post(
        "/knowledge-bases", json={"slug": "papers", "name": "Papers"}
    )
    assert created.status_code == 201, created.text

    uploaded = await kb_client.post(
        "/knowledge-bases/papers/publications",
        files={"files": ("paper.pdf", b"%PDF-1.4 test", "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text

    async with AsyncSession(engine) as session:
        kb = (
            await session.exec(
                KnowledgeBaseTable.__table__.select().where(
                    KnowledgeBaseTable.slug == "papers"
                )
            )
        ).one()
    assert kb.build_status == "ready"
    assert kb.last_built_at is not None
    [pub] = kb.publications
    assert pub["filename"] == "paper.pdf"
    assert pub["index_status"] == "indexed"
    assert pub["indexed_at"] is not None
