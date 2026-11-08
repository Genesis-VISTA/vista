"""`rag_search` hit headers by file kind, and the pointer to `fetch_kb_file`."""

import pytest

from vista_mcp_server import rag_mcp

pytestmark = pytest.mark.unit

CITATIONS = {
    "paper.pdf": {"title": "A Paper", "authors": ["Ann Lee"], "year": 2020},
    "salts.csv": {"title": "salts.csv", "abstract": "Melting points of salts"},
    "notes.md": {"title": "notes.md", "abstract": ""},
}


@pytest.fixture
def handle(monkeypatch):
    monkeypatch.setattr(
        rag_mcp, "_get_citation_for_source", lambda h, name: CITATIONS.get(name)
    )
    return rag_mcp._KbHandle(
        slug="salts-kb", db_path=None, text_collection=None, citation_collection=None
    )


def _fmt(handle, *metas):
    return rag_mcp._format_results(
        handle, documents=[f"doc{i}" for i in range(len(metas))], metadatas=list(metas)
    )


def test_pdf_hit_is_unchanged(handle):
    out = _fmt(handle, {"source": "paper.pdf", "page": 3})
    assert out.startswith(
        "[1] paper.pdf, page 3\n    Citation: A Paper — Ann Lee — (2020)"
    )
    assert "fetch_kb_file" not in out and "Description" not in out


def test_csv_row_window(handle):
    out = _fmt(handle, {"source": "salts.csv", "kind": "csv", "rows": "51-100"})
    assert "[1] salts.csv, rows 51-100 (CSV)" in out
    assert "Description: Melting points of salts" in out
    assert 'Full file: fetch_kb_file(kb_slug="salts-kb", filename="salts.csv")' in out
    assert "Citation:" not in out


def test_csv_summary(handle):
    out = _fmt(handle, {"source": "salts.csv", "kind": "csv", "rows": "summary"})
    assert "[1] salts.csv, summary (CSV)" in out


def test_tsv_label(handle):
    out = _fmt(handle, {"source": "salts.csv", "kind": "tsv", "rows": "1-50"})
    assert "rows 1-50 (TSV)" in out


def test_markdown_hit_without_description(handle):
    out = _fmt(handle, {"source": "notes.md", "kind": "markdown", "chunk_id": 0})
    assert "[1] notes.md, passage 1 (Markdown)" in out
    assert "Description" not in out
    assert 'filename="notes.md"' in out


def test_text_label(handle):
    out = _fmt(handle, {"source": "notes.txt", "kind": "text", "chunk_id": 4})
    assert "passage 5 (Text)" in out


def test_docstring_no_longer_forbids_quantitative():
    tool = rag_mcp.rag_search
    doc = getattr(tool, "fn", tool).__doc__
    assert "Do NOT use this for quantitative" not in doc
    assert "fetch_kb_file" in doc
