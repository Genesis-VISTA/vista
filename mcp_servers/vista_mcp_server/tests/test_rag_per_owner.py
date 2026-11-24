"""
A knowledge base's store is keyed by owner on disk: `<slug>/` for `local` and the
global ones, `<user_id>/<slug>/` for a hosted user. The server learns the user from
the tool metadata (multi-user-hosted, 15.1).
"""

import chromadb
import pytest
from chromadb.config import Settings as ChromaSettings

from vista_mcp_server import rag_mcp
from vista_mcp_server.config import settings

pytestmark = pytest.mark.unit

ALICE = "11111111-1111-4111-8111-111111111111"
BOB = "22222222-2222-4222-8222-222222222222"


def _make(root, *parts):
    db_path = root.joinpath("knowledge-bases", *parts, "rag_db")
    db_path.mkdir(parents=True)
    with chromadb.PersistentClient(
        path=str(db_path), settings=ChromaSettings(anonymized_telemetry=False)
    ) as client:
        client.get_or_create_collection(
            "text_chunks", metadata={"hnsw:space": "cosine"}
        )
    return db_path.resolve()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(rag_mcp, "_kbs", {})
    yield tmp_path
    for handle in list(rag_mcp._kbs.values()):
        rag_mcp._close(handle)


def test_two_users_same_slug_get_their_own_store(data_dir):
    a = _make(data_dir, ALICE, "papers")
    b = _make(data_dir, BOB, "papers")

    mine, err = rag_mcp._resolve_kb("papers", ALICE)
    assert err is None and mine is not None and mine.db_path == a
    his, err = rag_mcp._resolve_kb("papers", BOB)
    assert err is None and his is not None and his.db_path == b


def test_a_user_cannot_reach_anothers_store(data_dir):
    _make(data_dir, ALICE, "secret")
    handle, err = rag_mcp._resolve_kb("secret", BOB)
    assert handle is None and err is not None and "secret" not in err
    handle, err = rag_mcp._resolve_kb("secret", None)  # local sees no user folders
    assert handle is None and err is not None


@pytest.mark.parametrize("slug", ["../x", f"{ALICE}/secret", "a/b", ".."])
def test_a_slug_cannot_leave_the_folder(data_dir, slug):
    _make(data_dir, ALICE, "secret")
    handle, err = rag_mcp._resolve_kb(slug, BOB)
    assert handle is None and "not a Knowledge Base slug" in (err or "")


def test_a_bad_user_id_counts_as_local(data_dir):
    _make(data_dir, "papers")
    handle, err = rag_mcp._resolve_kb("papers", "../..")
    assert err is None and handle is not None


def test_global_and_local_stores_stay_where_they_are(data_dir):
    docs = _make(data_dir, "olcf-docs")
    for user in (ALICE, "local", None):
        handle, err = rag_mcp._resolve_kb("olcf-docs", user)
        assert err is None and handle is not None and handle.db_path == docs


def test_own_kb_wins_over_a_global_one_and_choices_are_the_users(data_dir):
    _make(data_dir, "papers")
    mine = _make(data_dir, ALICE, "papers")
    _make(data_dir, BOB, "bobs-only")
    handle, _ = rag_mcp._resolve_kb("papers", ALICE)
    assert handle is not None and handle.db_path == mine
    _, err = rag_mcp._resolve_kb(None, ALICE)
    assert err is None or "bobs-only" not in err
    assert sorted(rag_mcp._visible_kbs(ALICE)) == ["papers"]
