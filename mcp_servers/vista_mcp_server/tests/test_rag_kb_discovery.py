"""
A knowledge base created after the server started is searchable without a restart.

`rag_search` used to know only the KBs found at startup, so one created and
indexed in the UI answered "unknown kb_slug" until the stack was restarted,
and the agent fell back to whatever other KB it was offered.
"""

import shutil
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
    # Closed, as the backend's writers close theirs: ChromaDB keeps one client
    # per path for the whole process, and a store built again at this path
    # would otherwise be written through the old one.
    with chromadb.PersistentClient(
        path=str(db_path), settings=ChromaSettings(anonymized_telemetry=False)
    ) as client:
        client.get_or_create_collection(
            "text_chunks", metadata={"hnsw:space": "cosine"}
        )


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


# ---------------------------------------------------------------------------
# A KB changed under a running server: replaced by an import, deleted, rebuilt,
# reindexed. Each search checks its folder first (`_refresh_kb`).
# ---------------------------------------------------------------------------


def _add_chunk(db_path, chunk_id: str, text: str) -> None:
    client = chromadb.PersistentClient(
        path=str(db_path), settings=ChromaSettings(anonymized_telemetry=False)
    )
    try:
        client.get_or_create_collection(
            "text_chunks", metadata={"hnsw:space": "cosine"}
        ).add(ids=[chunk_id], documents=[text], embeddings=[[0.1, 0.2, 0.3]])
    finally:
        client.close()


def _import_store(root, slug, name):
    """A store in the backend's import layout: `<slug>/<name>/rag_db`, named by CURRENT."""
    kb_dir = root / "knowledge-bases" / slug
    db_path = kb_dir / name / "rag_db"
    db_path.mkdir(parents=True)
    _add_chunk(db_path, f"{name}_text_0", f"chunk of {name}")
    (kb_dir / "CURRENT").write_text(name, encoding="utf-8")
    return db_path


def _corpus(db_path, text: str) -> None:
    (db_path / "bm25_corpus.json").write_text(
        '{"version": 1, "documents": [{"id": "c0", "text": "%s"}]}' % text,
        encoding="utf-8",
    )


def test_an_imported_store_is_found_by_its_current_file(data_dir):
    store = _import_store(data_dir, "salt-papers", "store-aaaa")
    # A leftover canonical store beside it is not what the KB serves.
    _make_kb(data_dir, "salt-papers")

    handle, err = rag_mcp._resolve_kb("salt-papers")

    assert err is None and handle is not None
    assert handle.db_path == store.resolve()
    assert handle.text_collection.count() == 1


def test_a_current_file_naming_no_store_is_no_kb(data_dir):
    kb_dir = data_dir / "knowledge-bases" / "salt-papers"
    _make_kb(data_dir, "salt-papers")
    (kb_dir / "CURRENT").write_text("../elsewhere", encoding="utf-8")

    assert rag_mcp._discover_kb_paths() == []


def test_a_replaced_store_is_reopened(data_dir):
    old = _import_store(data_dir, "salt-papers", "store-aaaa")
    handle, _ = rag_mcp._resolve_kb("salt-papers")
    assert handle is not None and handle.db_path == old.resolve()
    old_client = handle.client

    new = _import_store(data_dir, "salt-papers", "store-bbbb")
    _add_chunk(new, "extra_text_0", "another chunk")
    handle, err = rag_mcp._resolve_kb("salt-papers")

    assert err is None and handle is not None
    assert handle.db_path == new.resolve()
    assert handle.text_collection.count() == 2
    assert old_client._closed


def test_a_deleted_kb_is_dropped(data_dir):
    import shutil

    _make_kb(data_dir, "salt-papers")
    _make_kb(data_dir, "other-papers")
    rag_mcp._register_new_kbs()
    handle = rag_mcp._kbs["salt-papers"]
    rag_mcp._close(handle)  # as Windows needs before the folder can go
    shutil.rmtree(data_dir / "knowledge-bases" / "salt-papers")

    found, err = rag_mcp._resolve_kb("salt-papers")

    assert found is None
    assert "unknown kb_slug 'salt-papers'" in err
    assert sorted(rag_mcp._kbs) == ["other-papers"]


def test_a_store_rebuilt_at_the_same_path_is_reopened(data_dir):
    import shutil

    _make_kb(data_dir, "salt-papers")
    first, _ = rag_mcp._resolve_kb("salt-papers")
    assert first is not None and first.text_collection.count() == 0
    rag_mcp._close(first)
    shutil.rmtree(data_dir / "knowledge-bases" / "salt-papers")
    _make_kb(data_dir, "salt-papers")
    db_path = data_dir / "knowledge-bases" / "salt-papers" / "rag_db"
    # Indexed, which writes the corpus, as the backend's indexer does.
    _add_chunk(db_path, "c0", "new")
    _corpus(db_path, "new")

    handle, err = rag_mcp._resolve_kb("salt-papers")

    assert err is None and handle is not None and handle is not first
    assert handle.text_collection.count() == 1


def test_a_rewritten_bm25_corpus_is_reloaded(data_dir):
    _make_kb(data_dir, "salt-papers")
    db_path = data_dir / "knowledge-bases" / "salt-papers" / "rag_db"
    handle, _ = rag_mcp._resolve_kb("salt-papers")
    assert handle is not None and handle.bm25 is None

    _corpus(db_path, "molten fluoride salt")
    handle, _ = rag_mcp._resolve_kb("salt-papers")
    assert handle is not None and handle.bm25 is not None
    assert handle.bm25.query("fluoride")[0].chunk_id == "c0"
    client = handle.client

    # Written again in one step, as `build_rag.write_bm25_corpus` does.
    tmp = db_path / ".bm25.tmp"
    tmp.write_text(
        '{"version": 1, "documents": [{"id": "c1", "text": "chloride salt"}]}',
        encoding="utf-8",
    )
    tmp.replace(db_path / "bm25_corpus.json")
    handle, _ = rag_mcp._resolve_kb("salt-papers")
    assert handle is not None
    assert handle.bm25.query("chloride")[0].chunk_id == "c1"
    assert handle.client is not client and client._closed


def test_a_store_written_elsewhere_is_reopened(data_dir):
    """Indexing in the backend writes the store, which an open client does not see."""
    _make_kb(data_dir, "salt-papers")
    db_path = data_dir / "knowledge-bases" / "salt-papers" / "rag_db"
    handle, _ = rag_mcp._resolve_kb("salt-papers")
    assert handle is not None
    # Searching it again is not a change.
    assert rag_mcp._resolve_kb("salt-papers")[0] is handle

    # An indexing run, which ends by rewriting the corpus.
    _add_chunk(db_path, "c1", "added by the backend")
    _corpus(db_path, "added by the backend")
    reopened, _ = rag_mcp._resolve_kb("salt-papers")

    assert reopened is not None and reopened is not handle
    assert reopened.text_collection.count() == 1


def test_a_store_only_read_elsewhere_is_not_reopened(data_dir):
    """
    The backend opens a store to read its citations each time it lists its
    knowledge bases, and opening writes `chroma.sqlite3`: not a change.
    """
    _make_kb(data_dir, "salt-papers")
    db_path = data_dir / "knowledge-bases" / "salt-papers" / "rag_db"
    _corpus(db_path, "molten salt")
    handle, _ = rag_mcp._resolve_kb("salt-papers")
    assert handle is not None
    rag_mcp._close(handle)  # as if from another process: no shared client
    before = (db_path / "chroma.sqlite3").stat().st_mtime_ns
    with chromadb.PersistentClient(
        path=str(db_path), settings=ChromaSettings(anonymized_telemetry=False)
    ) as reader:
        reader.get_collection("citations").get(include=["metadatas"])
    assert (db_path / "chroma.sqlite3").stat().st_mtime_ns != before

    assert rag_mcp._resolve_kb("salt-papers")[0] is handle


def test_without_a_slug_only_the_kb_searched_is_reopened(data_dir):
    """Choosing among several reopens none; a deleted one leaves the choices."""
    for slug in ("salt-papers", "alloy-papers", "gone-papers"):
        _make_kb(data_dir, slug)
    rag_mcp._register_new_kbs()
    handles = dict(rag_mcp._kbs)
    for slug in ("salt-papers", "alloy-papers"):
        _corpus(data_dir / "knowledge-bases" / slug / "rag_db", "reindexed")
    shutil.rmtree(data_dir / "knowledge-bases" / "gone-papers")

    handle, err = rag_mcp._resolve_kb(None)

    assert handle is None
    assert "alloy-papers, salt-papers." in err
    assert sorted(rag_mcp._kbs) == ["alloy-papers", "salt-papers"]
    assert all(rag_mcp._kbs[s] is handles[s] for s in rag_mcp._kbs)

    # With one left, it is the one searched, and so is brought up to date.
    shutil.rmtree(data_dir / "knowledge-bases" / "alloy-papers")
    handle, err = rag_mcp._resolve_kb(None)
    assert err is None and handle is not None
    assert handle.slug == "salt-papers" and handle is not handles["salt-papers"]


def test_a_search_reads_the_store_an_import_switched_to(data_dir, monkeypatch):
    """`rag_search`'s own path: off the event loop, the KB refreshed first."""
    monkeypatch.setattr(rag_mcp, "_embed", lambda text: [0.1, 0.2, 0.3])
    _import_store(data_dir, "salt-papers", "store-aaaa")
    assert "chunk of store-aaaa" in rag_mcp._search(
        "salt", "salt-papers", 5, False, 0.5
    )

    _import_store(data_dir, "salt-papers", "store-bbbb")
    found = rag_mcp._search("salt", "salt-papers", 5, False, 0.5)

    assert "chunk of store-bbbb" in found
    assert "chunk of store-aaaa" not in found
