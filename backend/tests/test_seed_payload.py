"""
Seeding from a bundled payload directory instead of a vista-data GitLab token.

The prebuilt package ships the vista-data files rather than a credential, so
`seed.LocalRepoClient` resolves the same repo-relative paths from disk. These
tests pin three things: the client copies faithfully and resumably, the branch
that selects it yields the same rows as the token path, and a knowledge base
whose vector store is absent or empty is refused rather than recorded as ready.

Hermetic. The vector store is created directly with explicit embedding vectors,
so no model loads and nothing reaches the network -- and because
`_build_knowledge_base` returns early when `chroma.sqlite3` already exists, this
is also the real prebuilt-package path, where the launcher places the store
before seeding runs.
"""

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.config import settings
from vista_backend.db import seed as seed_module
from vista_backend.db.schemas import KnowledgeBaseTable, ProjectTable, SkillTable
from vista_backend.db.seed import LocalRepoClient, seed_db

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

# Every vista-data path `seed_db` asks for, with the mstdb assets the three
# asset-dependent skills depend on. A payload missing any of these is a
# packaging defect, and `LocalRepoClient` raises rather than seeding partially.
PAYLOAD_FILES = {
    "mstdb/Molten_Salt_Thermophysical_Properties.csv": "salt,tm\nFLiBe,732\n",
    "mstdb/Molten_Salt_Thermophysical_Properties.json": '{"salts": []}',
    "mstdb/elemental-properties.csv": "element,z\nLi,3\n",
    "molten-salt-papers/paper-one.pdf": "%PDF-1.4 one",
    "molten-salt-papers/nested/paper-two.pdf": "%PDF-1.4 two",
}

# `build_rag.TextRAG` writes 640-dimension vectors from
# `microsoft/harrier-oss-v1-270m`; a collection locks to the dimension of its
# first insert, so a stand-in store has to match or a later real write fails.
EMBEDDING_DIM = 640


def make_payload(root):
    """Write a vista-data payload tree, including a nested paper."""
    for rel, text in PAYLOAD_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def make_vector_store(rag_db, chunks: int = 3):
    """
    Create a Chroma store holding `chunks` text chunks, as the packaging build
    would ship. Embeddings are passed explicitly -- Chroma's default embedding
    function would otherwise download an ONNX model on first call.
    """
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    rag_db.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(
        path=str(rag_db), settings=ChromaSettings(anonymized_telemetry=False)
    )
    collection = client.get_or_create_collection(
        name="text_chunks", metadata={"hnsw:space": "cosine"}
    )
    if chunks:
        collection.add(
            ids=[f"chunk-{i}" for i in range(chunks)],
            embeddings=[[0.1] * EMBEDDING_DIM for _ in range(chunks)],
            documents=[f"chunk {i} text" for i in range(chunks)],
            metadatas=[{"filename": "paper-one.pdf"} for _ in range(chunks)],
        )
    return rag_db


async def run_seed(engine):
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    await seed_db(engine)


def make_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fks(dbapi_connection, _):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


@pytest.fixture
def payload_env(request, tmp_path, monkeypatch):
    """
    A data dir, a payload tree, and a prebuilt vector store already in place.

    The store's chunk count comes from an indirect parameter -- 3 by default, 0
    for an indexless store, `None` for no store at all. Set up front rather than
    by emptying an existing store, because Chroma shares one client per path
    within a process and a store replaced underneath it is not readable.

    `seed.REPO_ROOT` is redirected at tmp_path because the mstdb CSV copy
    targets `hpc_jobs/forge-tune/`, which is gitignored -- on a fresh clone the
    file is absent and an unpatched test would write into the checkout.
    """
    chunks = getattr(request, "param", 3)
    data_dir = tmp_path / "data"
    payload = make_payload(tmp_path / "payload")
    kb_dir = data_dir / "knowledge-bases" / "molten-salt-papers"
    if chunks is not None:
        make_vector_store(kb_dir / "rag_db", chunks=chunks)

    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "vista_data_token", None)
    monkeypatch.setattr(settings, "vista_data_payload_dir", payload)
    monkeypatch.setattr(seed_module, "REPO_ROOT", tmp_path / "repo")
    return {"data_dir": data_dir, "payload": payload, "kb_dir": kb_dir}


# --------------------------------------------------------------------------
# LocalRepoClient (4.2)
# --------------------------------------------------------------------------


async def test_local_client_copies_a_file(tmp_path):
    payload = make_payload(tmp_path / "payload")
    dest = tmp_path / "out" / "mstdb.csv"
    async with LocalRepoClient(payload) as client:
        await client.download_file(
            "mstdb/Molten_Salt_Thermophysical_Properties.csv", dest
        )
    assert (
        dest.read_text(encoding="utf-8")
        == (PAYLOAD_FILES["mstdb/Molten_Salt_Thermophysical_Properties.csv"])
    )
    # The rename-from-.part staging leaves nothing behind.
    assert not list(dest.parent.glob("*.part"))


async def test_local_client_copies_a_nested_directory_tree(tmp_path):
    payload = make_payload(tmp_path / "payload")
    dest = tmp_path / "out" / "pdfs"
    async with LocalRepoClient(payload) as client:
        await client.download_dir("molten-salt-papers", dest)
    assert (dest / "paper-one.pdf").read_text(encoding="utf-8") == "%PDF-1.4 one"
    assert (dest / "nested" / "paper-two.pdf").read_text(
        encoding="utf-8"
    ) == "%PDF-1.4 two"


async def test_local_client_skips_files_already_on_disk(tmp_path):
    """Resumable like the GitLab client: an existing file is left untouched."""
    payload = make_payload(tmp_path / "payload")
    dest = tmp_path / "out" / "pdfs"
    (dest / "nested").mkdir(parents=True)
    (dest / "nested" / "paper-two.pdf").write_text(
        "kept from a prior run", encoding="utf-8"
    )

    async with LocalRepoClient(payload) as client:
        await client.download_dir("molten-salt-papers", dest)

    assert (dest / "nested" / "paper-two.pdf").read_text(
        encoding="utf-8"
    ) == "kept from a prior run"
    assert (dest / "paper-one.pdf").read_text(encoding="utf-8") == "%PDF-1.4 one"


async def test_local_client_reports_a_missing_payload_file(tmp_path):
    payload = make_payload(tmp_path / "payload")
    async with LocalRepoClient(payload) as client:
        with pytest.raises(FileNotFoundError, match="mstdb/absent.csv"):
            await client.download_file("mstdb/absent.csv", tmp_path / "out.csv")


async def test_local_client_reports_a_missing_payload_root(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not exist"):
        async with LocalRepoClient(tmp_path / "not-there"):
            pass


async def test_local_client_refuses_a_path_escaping_the_payload(tmp_path):
    payload = make_payload(tmp_path / "payload")
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    async with LocalRepoClient(payload) as client:
        with pytest.raises(ValueError, match="escapes the payload root"):
            await client.download_file("../outside.txt", tmp_path / "out.txt")


# --------------------------------------------------------------------------
# Branch selection (4.3)
# --------------------------------------------------------------------------


async def test_payload_wins_when_both_payload_and_token_are_set(
    payload_env, monkeypatch
):
    """
    Deterministic precedence: the payload is already on disk, so no GitLab
    client is constructed even with a token present.
    """
    monkeypatch.setattr(settings, "vista_data_token", "glpat-would-be-used")

    def _fail(*args, **kwargs):
        raise AssertionError("GitlabRepoClient constructed despite a payload path")

    monkeypatch.setattr(seed_module, "GitlabRepoClient", _fail)

    engine = make_engine()
    try:
        await run_seed(engine)
        async with AsyncSession(engine) as session:
            rows = (await session.exec(select(KnowledgeBaseTable))).all()
        assert [row.slug for row in rows] == ["molten-salt-papers"]
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------
# Parity with the token path (4.5)
# --------------------------------------------------------------------------


@pytest.fixture(params=["payload", "token"])
async def seeded_either_source(request, payload_env, monkeypatch):
    """
    Seed once per source and hand back the session.

    The token variant substitutes a client that reads the same payload tree, so
    the two runs differ only in which `ctx_manager` branch `seed_db` takes --
    which is what parity is being asserted about. Fetching over HTTP is covered
    by `GitlabRepoClient`'s own paths, not here.
    """
    if request.param == "token":
        payload = payload_env["payload"]
        monkeypatch.setattr(settings, "vista_data_payload_dir", None)
        monkeypatch.setattr(settings, "vista_data_token", "glpat-stub")
        monkeypatch.setattr(
            seed_module,
            "GitlabRepoClient",
            lambda domain, repo, token=None: LocalRepoClient(payload),
        )

    engine = make_engine()
    await run_seed(engine)
    async with AsyncSession(engine) as session:
        yield session
    await engine.dispose()


async def test_knowledge_base_row_matches_the_token_path(seeded_either_source):
    rows = (await seeded_either_source.exec(select(KnowledgeBaseTable))).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.slug == "molten-salt-papers"
    assert row.name == "Molten Salt Papers"
    assert row.shared_with_mcp is True
    assert row.build_status == "ready"
    assert row.pdfs_dir.endswith("knowledge-bases/molten-salt-papers/pdfs")
    assert row.rag_db_path.endswith("knowledge-bases/molten-salt-papers/rag_db")


async def test_project_knowledge_base_list_matches_the_token_path(
    seeded_either_source,
):
    project = (
        await seeded_either_source.exec(
            select(ProjectTable).where(ProjectTable.name == "molten-salt")
        )
    ).first()
    assert project is not None
    assert project.knowledge_bases == ["molten-salt-papers"]


async def test_asset_dependent_skills_match_the_token_path(seeded_either_source):
    """The three skills skipped on the tokenless path are all registered."""
    names = {
        row.name for row in (await seeded_either_source.exec(select(SkillTable))).all()
    }
    assert {"salt-analysis", "model-fine-tuning", "salt-prediction"}.issubset(names)


async def test_skill_assets_are_on_disk(seeded_either_source, payload_env):
    """Each asset-dependent skill's storage dir carries its mstdb file."""
    assets = sorted(
        p.name
        for p in (payload_env["data_dir"] / "storage").rglob("assets/*")
        if p.is_file()
    )
    assert assets == [
        "Molten_Salt_Thermophysical_Properties.csv",
        "Molten_Salt_Thermophysical_Properties.json",
        "Molten_Salt_Thermophysical_Properties.json",
        "elemental-properties.csv",
    ]


async def test_pdfs_are_copied_for_citation_display(seeded_either_source, payload_env):
    """
    A cited paper has to be openable from the interface, so the PDFs land in the
    knowledge base's own folder, nested layout preserved.
    """
    pdfs = payload_env["kb_dir"] / "pdfs"
    assert (pdfs / "paper-one.pdf").is_file()
    assert (pdfs / "nested" / "paper-two.pdf").is_file()


# --------------------------------------------------------------------------
# Non-empty index assertion (4.4)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("payload_env", [0], indirect=True)
async def test_empty_vector_store_is_refused(payload_env):
    """An indexless store must fail loudly, not seed `build_status="ready"`."""
    engine = make_engine()
    try:
        with pytest.raises(RuntimeError, match="molten-salt-papers") as exc:
            await run_seed(engine)
        assert "empty vector store" in str(exc.value)
        async with AsyncSession(engine) as session:
            assert (await session.exec(select(KnowledgeBaseTable))).all() == []
    finally:
        await engine.dispose()


@pytest.mark.parametrize("payload_env", [None], indirect=True)
async def test_absent_vector_store_is_refused(payload_env, monkeypatch):
    """
    With no store and no PDFs to build one from, seeding stops. The PDF copy is
    suppressed so this is the packaging failure -- a payload whose store never
    made it in -- rather than an indexing run.
    """

    async def _no_pdfs(self, repo_dir, dest):
        dest.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(LocalRepoClient, "download_dir", _no_pdfs)

    engine = make_engine()
    try:
        with pytest.raises(RuntimeError, match="no vector store") as exc:
            await run_seed(engine)
        assert "molten-salt-papers" in str(exc.value)
    finally:
        await engine.dispose()
