"""
A knowledge base created after the server started is searchable without a restart.

`rag_search` used to know only the KBs found at startup, so one created and
indexed in the UI answered "unknown kb_slug" until the stack was restarted,
and the agent fell back to whatever other KB it was offered.
"""

import chromadb
import pytest
from chromadb.config import Settings as ChromaSettings

from vista_mcp_server import rag_mcp
from vista_mcp_server.config import settings

pytestmark = pytest.mark.unit


def _make_kb(root, slug):
    """An indexed KB on disk, in the backend's `<slug>/rag_db/` layout."""
    db_path = root / "knowledge-bases" / slug / "rag_db"
    db_path.mkdir(parents=True)
    client = chromadb.PersistentClient(
        path=str(db_path), settings=ChromaSettings(anonymized_telemetry=False)
    )
    client.get_or_create_collection("text_chunks", metadata={"hnsw:space": "cosine"})


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(rag_mcp, "_kbs", {})
    return tmp_path


def test_kb_created_after_startup_is_found(data_dir):
    _make_kb(data_dir, "molten-salt-papers")
    assert rag_mcp._register_new_kbs() == ["molten-salt-papers"]

    _make_kb(data_dir, "nobel-prizes-2026")
    handle, err = rag_mcp._resolve_kb("nobel-prizes-2026")

    assert err is None
    assert handle is not None and handle.slug == "nobel-prizes-2026"
    assert sorted(rag_mcp._kbs) == ["molten-salt-papers", "nobel-prizes-2026"]


def test_first_kb_created_after_an_empty_startup_is_found(data_dir):
    assert rag_mcp._register_new_kbs() == []

    _make_kb(data_dir, "nobel-prizes-2026")
    handle, err = rag_mcp._resolve_kb(None)

    assert err is None
    assert handle is not None and handle.slug == "nobel-prizes-2026"


def test_unknown_kb_still_reports_the_available_ones(data_dir):
    _make_kb(data_dir, "molten-salt-papers")
    rag_mcp._register_new_kbs()

    handle, err = rag_mcp._resolve_kb("no-such-kb")

    assert handle is None
    assert "unknown kb_slug 'no-such-kb'" in err
    assert "molten-salt-papers" in err
