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

from pathlib import Path

import httpx
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.config import settings
from vista_backend.db.schemas import KnowledgeBaseTable

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture
async def kb_client(engine, tmp_path, monkeypatch):
    from vista_backend.api import knowledge_bases as kb_api
    from vista_backend.api.api import app
    from vista_backend.db.db import get_engine

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


async def _make_kb(kb_client, slug="papers"):
    created = await kb_client.post(
        "/knowledge-bases", json={"slug": slug, "name": slug.title()}
    )
    assert created.status_code == 201, created.text


@pytest.mark.parametrize(
    "name", ["paper.pdf", "table.csv", "table.tsv", "notes.txt", "README.md"]
)
async def test_each_accepted_format_is_stored_under_its_own_name(
    kb_client, tmp_path, name
):
    await _make_kb(kb_client)
    uploaded = await kb_client.post(
        "/knowledge-bases/papers/publications",
        files={"files": (name, b"a,b\n1,2\n", "application/octet-stream")},
    )
    assert uploaded.status_code == 201, uploaded.text
    [pub] = uploaded.json()["publications"]
    assert pub["filename"] == name
    kinds = {
        ".pdf": "pdf",
        ".csv": "csv",
        ".tsv": "tsv",
        ".txt": "text",
        ".md": "markdown",
    }
    assert pub["kind"] == kinds[Path(name).suffix]
    stored = list((tmp_path / "knowledge-bases").rglob(name))
    assert len(stored) == 1


async def test_unsupported_format_is_rejected_and_nothing_stored(kb_client, tmp_path):
    await _make_kb(kb_client)
    uploaded = await kb_client.post(
        "/knowledge-bases/papers/publications",
        files={"files": ("book.xlsx", b"PK", "application/octet-stream")},
    )
    assert uploaded.status_code == 400
    detail = uploaded.json()["detail"]
    for fmt in ("PDF", "CSV", "TSV", "TXT", "MD"):
        assert fmt in detail
    assert not list((tmp_path / "knowledge-bases").rglob("book.xlsx*"))
    kb = await kb_client.get("/knowledge-bases/papers")
    assert kb.json()["publications"] == []


async def test_download_uses_the_files_content_type(kb_client):
    await _make_kb(kb_client)
    await kb_client.post(
        "/knowledge-bases/papers/publications",
        files={"files": ("table.csv", b"a,b\n1,2\n", "application/octet-stream")},
    )
    await kb_client.post(
        "/knowledge-bases/papers/publications",
        files={"files": ("paper.pdf", b"%PDF-1.4", "application/octet-stream")},
    )
    csv_resp = await kb_client.get("/knowledge-bases/papers/publications/table.csv")
    assert csv_resp.headers["content-type"].startswith("text/csv")
    pdf_resp = await kb_client.get("/knowledge-bases/papers/publications/paper.pdf")
    assert pdf_resp.headers["content-type"] == "application/pdf"


async def test_delete_removes_the_files_index_rows(kb_client, monkeypatch):
    from vista_backend.api import knowledge_bases as kb_api

    deleted: list[str] = []

    async def _delete_rows(rag_db_path, pdfs_dir, filename):
        deleted.append(filename)
        return 2

    monkeypatch.setattr(kb_api.indexer, "delete_publication_rows", _delete_rows)
    await _make_kb(kb_client)
    await kb_client.post(
        "/knowledge-bases/papers/publications",
        files={"files": ("table.csv", b"a,b\n1,2\n", "text/csv")},
    )
    resp = await kb_client.delete("/knowledge-bases/papers/publications/table.csv")
    assert resp.status_code == 204
    assert deleted == ["table.csv"]
    kb = await kb_client.get("/knowledge-bases/papers")
    assert [p["filename"] for p in kb.json()["publications"]] == []


def test_backend_suffix_list_mirrors_build_rag():
    import sys

    from vista_backend.utils import indexer

    root = str(Path(__file__).resolve().parents[2])
    sys.path.insert(0, root)
    try:
        import build_rag
    finally:
        sys.path.remove(root)
    assert indexer.ACCEPTED_SUFFIXES == build_rag.ACCEPTED_SUFFIXES
    assert indexer.SUFFIX_KIND == build_rag.SUFFIX_KIND


def test_reconciler_reads_kind_row_count_and_description():
    from vista_backend.api import knowledge_bases as kb_api
    from vista_backend.db.schemas import Publication

    pub = Publication(filename="runs.csv")
    kb_api._merge_chroma_metadata_into_pub(
        pub,
        {
            "source": "runs.csv",
            "title": "runs.csv",
            "abstract": "Viscosity runs",
            "kind": "csv",
            "row_count": 120,
            "record": "dataset",
        },
    )
    assert pub.kind == "csv"
    assert pub.row_count == 120
    assert pub.abstract == "Viscosity runs"

    legacy = Publication(filename="old.pdf")
    kb_api._merge_chroma_metadata_into_pub(
        legacy, {"source": "old.pdf", "title": "Old paper"}
    )
    assert legacy.kind == "pdf"
    assert legacy.row_count is None


def test_disk_reconcile_picks_up_every_accepted_suffix(tmp_path):
    from vista_backend.api import knowledge_bases as kb_api

    for name in ("a.pdf", "b.CSV", "c.tsv", "d.txt", "e.md", "f.xlsx"):
        (tmp_path / name).write_bytes(b"x")
    pubs, changed = kb_api._reconcile_with_disk(str(tmp_path), [])
    assert changed
    assert {p["filename"]: p["kind"] for p in pubs} == {
        "a.pdf": "pdf",
        "b.CSV": "csv",
        "c.tsv": "tsv",
        "d.txt": "text",
        "e.md": "markdown",
    }


def test_reconciler_row_count_without_a_description():
    from vista_backend.api import knowledge_bases as kb_api
    from vista_backend.db.schemas import Publication

    pub = Publication(filename="runs.csv")
    kb_api._merge_chroma_metadata_into_pub(
        pub,
        {
            "source": "runs.csv",
            "title": "runs.csv",
            "abstract": "",
            "kind": "csv",
            "row_count": 7,
            "record": "dataset",
        },
    )
    assert pub.row_count == 7
    assert pub.kind == "csv"
    assert not pub.abstract
