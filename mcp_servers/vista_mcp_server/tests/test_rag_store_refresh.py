"""
`rag_search` sees what another process did to a KB's store after it was opened.

The backend indexes and deletes files in the same Chroma store the MCP server
holds open. The server's in-memory vector index used to keep a deleted file's
ids (the query matched them, the rows were gone, and every hit printed as
`unknown, page ?` / `None`) and to miss files indexed after the KB was opened.
These tests do the writes from a subprocess, as the backend does.
"""

import asyncio
import subprocess
import sys

import pytest

from vista_mcp_server import rag_mcp
from vista_mcp_server.config import settings

pytestmark = pytest.mark.unit

# Runs in a child process: argv = db_path, action ("build" | "delete" | "add").
_WRITER = r"""
import sys
import chromadb
from chromadb.config import Settings

path, action = sys.argv[1], sys.argv[2]
client = chromadb.PersistentClient(path=path, settings=Settings(anonymized_telemetry=False))
col = client.get_or_create_collection("text_chunks", metadata={"hnsw:space": "cosine"})
if action == "build":
    col.add(
        ids=["old-0", "old-1", "keep-0"],
        embeddings=[[1.0, 0.0, 0.0], [0.9, 0.1, 0.0], [0.0, 1.0, 0.0]],
        documents=["old zero", "old one", "kept text"],
        metadatas=[
            {"source": "old.csv", "kind": "csv", "rows": "1-2"},
            {"source": "old.csv", "kind": "csv", "rows": "3-4"},
            {"source": "keep.md", "kind": "markdown", "chunk_id": 0},
        ],
    )
elif action == "delete":
    col.delete(where={"source": "old.csv"})
elif action == "add":
    col.add(
        ids=["new-0"],
        embeddings=[[0.95, 0.05, 0.0]],
        documents=["brand new text"],
        metadatas=[{"source": "new.md", "kind": "markdown", "chunk_id": 0}],
    )
"""


def _writer(db_path, action):
    subprocess.run(
        [sys.executable, "-c", _WRITER, str(db_path), action],
        check=True,
        encoding="utf-8",
        capture_output=True,
    )


@pytest.fixture
def kb(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(rag_mcp, "_kbs", {})
    monkeypatch.setattr(rag_mcp, "_embed", lambda text: [1.0, 0.0, 0.0])
    monkeypatch.setattr(rag_mcp, "_get_citation_for_source", lambda h, s: None)
    db_path = tmp_path / "knowledge-bases" / "salts" / "rag_db"
    db_path.mkdir(parents=True)
    _writer(db_path, "build")
    assert rag_mcp._register_new_kbs() == ["salts"]
    return db_path


def _search():
    tool = rag_mcp.rag_search
    fn = getattr(tool, "fn", tool)
    return asyncio.run(fn("anything", kb_slug="salts", n_results=10))


def test_deleted_file_leaves_no_ghost_hits(kb):
    assert "old.csv" in _search()  # the handle is open and has seen the rows

    _writer(kb, "delete")
    out = _search()

    assert "unknown" not in out and "None" not in out
    assert "old.csv" not in out
    assert "keep.md" in out


def test_file_indexed_after_open_is_found(kb):
    assert "new.md" not in _search()

    _writer(kb, "add")

    assert "new.md" in _search()


def test_unchanged_store_keeps_its_handle(kb):
    first = rag_mcp._kbs["salts"]
    _search()
    _search()
    assert rag_mcp._kbs["salts"] is first


def test_hits_without_rows_are_dropped(caplog):
    handle = rag_mcp._KbHandle(
        slug="s", db_path=None, text_collection=None, citation_collection=None
    )
    caplog.set_level("INFO", logger=rag_mcp.logger.name)
    out = rag_mcp._format_results(
        handle,
        documents=[None, "", "real"],
        metadatas=[None, {"source": "a.md"}, {"source": "b.md", "kind": "markdown"}],
    )
    assert "unknown" not in out and "None" not in out
    assert out.startswith("[1] b.md")
    assert "dropped 2" in caplog.text
