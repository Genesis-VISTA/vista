"""
`sync_default_projects`: the hello-olcf default project and its knowledge base.

Hermetic. Payload tests read a vista-data tree in `tmp_path` with a prebuilt
Chroma store, so nothing is embedded; the token path is covered by faking the
GitLab client over a local folder, never by reaching it.
"""

import logging

import httpx
import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.config import settings
from vista_backend.db import seed as seed_module
from vista_backend.db.schemas import KnowledgeBaseTable, ProjectTable, SkillTable
from vista_backend.db.seed import (
    AI_SAFETY_KB_ID,
    AI_SAFETY_PROJECT_ID,
    HELLO_OLCF_PROJECT_ID,
    OLCF_DOCS_KB_ID,
    LocalRepoClient,
    seed_db,
    sync_bundled_skills,
    sync_default_projects,
)
from vista_backend.utils import indexer

from test_seed_payload import make_engine, make_payload, make_vector_store

pytestmark = [pytest.mark.anyio, pytest.mark.integration]

SKILLS = ["hello-odo", "hello-frontier", "hello-lux", "hpc-skill-author"]


def add_olcf_docs(payload, data_dir, chunks: int | None = 3):
    """Add `olcf-docs/` to a payload and, unless `chunks` is None, its prebuilt index."""
    doc = payload / "olcf-docs" / "systems__frontier_user_guide__running-jobs.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(
        "# Frontier User Guide > Running Jobs\n\n"
        "Source: https://docs.olcf.ornl.gov/systems/frontier_user_guide.html#running-jobs\n",
        encoding="utf-8",
    )
    (payload / "olcf-docs" / "SOURCE").write_text("abc123\n", encoding="utf-8")
    if chunks is not None:
        make_vector_store(data_dir / "knowledge-bases" / "olcf-docs" / "rag_db", chunks)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A payload dir (empty unless extended) wired into settings."""
    data_dir = tmp_path / "data"
    payload = tmp_path / "payload"
    payload.mkdir()
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "vista_data_token", None)
    monkeypatch.setattr(settings, "vista_data_payload_dir", payload)
    monkeypatch.setattr(settings, "seed_science_projects", False)
    monkeypatch.setattr(seed_module, "REPO_ROOT", tmp_path / "repo")
    return {"data_dir": data_dir, "payload": payload, "tmp": tmp_path}


@pytest.fixture
async def engine():
    from sqlmodel import SQLModel

    engine = make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def no_indexing(monkeypatch):
    async def _forbidden(**kwargs):
        raise AssertionError("the indexer ran")

    monkeypatch.setattr(indexer, "index_publications", _forbidden)


async def boot(engine):
    """`init_db`'s order: seed, the default project, then the bundled skills."""
    await seed_db(engine)
    await sync_default_projects(engine)
    await sync_bundled_skills(engine)


async def the_project(engine) -> ProjectTable | None:
    async with AsyncSession(engine) as session:
        return await session.get(ProjectTable, HELLO_OLCF_PROJECT_ID)


async def kb_slugs(engine) -> list[str]:
    async with AsyncSession(engine) as session:
        return sorted(
            k.slug for k in (await session.exec(select(KnowledgeBaseTable))).all()
        )


async def add_old_ai_safety(engine, data_dir):
    """The rows an earlier version seeded: the AI-safety project and its KB."""
    from vista_backend.db.seed import _seeded_kb

    kb_dir = data_dir / "knowledge-bases" / "ai-safety"
    (kb_dir / "pdfs").mkdir(parents=True)
    async with AsyncSession(engine) as session:
        session.add(
            _seeded_kb(
                AI_SAFETY_KB_ID,
                kb_dir,
                slug="ai-safety",
                name="AI Safety Papers",
                description="old",
            )
        )
        session.add(
            ProjectTable(
                id=AI_SAFETY_PROJECT_ID,
                name="ai-safety-autonomous-labs",
                description="old",
                system_prompt="old prompt",
                skills=[],
                knowledge_bases=["ai-safety"],
                tools=["*", "!agenthpc_*"],
                usage_limits=dict(request_limit=50),
            )
        )
        await session.commit()


async def ai_safety_rows(engine):
    async with AsyncSession(engine) as session:
        project = await session.get(ProjectTable, AI_SAFETY_PROJECT_ID)
        kb = await session.get(KnowledgeBaseTable, AI_SAFETY_KB_ID)
        return (
            project.model_dump_json() if project else None,
            kb.model_dump_json() if kb else None,
        )


# --------------------------------------------------------------------------
# First run
# --------------------------------------------------------------------------


async def test_fresh_db_gets_hello_olcf_with_its_kb_and_skills_and_no_ai_safety(
    env, engine, no_indexing
):
    add_olcf_docs(env["payload"], env["data_dir"])
    await boot(engine)

    project = await the_project(engine)
    assert project is not None
    assert project.name == "hello-olcf"
    assert project.knowledge_bases == ["olcf-docs"]
    assert project.skills == SKILLS
    assert project.tools == ["*", "!agenthpc_*"]
    assert project.usage_limits.get("request_limit") == 50
    assert "OLCF" in project.system_prompt

    async with AsyncSession(engine) as session:
        kb = await session.get(KnowledgeBaseTable, OLCF_DOCS_KB_ID)
        assert kb is not None
        assert (kb.slug, kb.name, kb.build_status) == (
            "olcf-docs",
            "OLCF User Documentation",
            "ready",
        )
        assert await session.get(ProjectTable, AI_SAFETY_PROJECT_ID) is None
        assert await session.get(KnowledgeBaseTable, AI_SAFETY_KB_ID) is None
        names = [p.name for p in (await session.exec(select(ProjectTable))).all()]
    assert names == ["hello-olcf"]
    assert await kb_slugs(engine) == ["olcf-docs"]
    kb_dir = env["data_dir"] / "knowledge-bases/olcf-docs"
    assert (kb_dir / "pdfs/systems__frontier_user_guide__running-jobs.md").is_file()


async def test_the_projects_skills_resolve_although_synced_after_it(
    env, engine, no_indexing
):
    """
    D5: the project is inserted before `sync_bundled_skills` runs, and holds the
    skills by name. The agent resolves them by name from the library when it is
    built, so by then all four are there.
    """
    add_olcf_docs(env["payload"], env["data_dir"])
    await sync_default_projects(engine)  # an upgrade: no seed_db, no skills yet
    async with AsyncSession(engine) as session:
        assert (await session.exec(select(SkillTable))).all() == []
    await sync_bundled_skills(engine)

    project = await the_project(engine)
    assert project is not None
    async with AsyncSession(engine) as session:
        library = {s.name for s in (await session.exec(select(SkillTable))).all()}
    assert set(project.skills) <= library


async def test_a_kb_seeded_again_ignores_a_current_file_left_behind(
    env, engine, no_indexing
):
    """
    A deletion that could not remove an imported store (on Windows, while the
    MCP server holds it open) leaves `CURRENT` naming it. Seeded again, the
    knowledge base is the store just seeded, not that one.
    """
    kb_dir = env["data_dir"] / "knowledge-bases" / "olcf-docs"
    (kb_dir / "store-old" / "rag_db").mkdir(parents=True)
    (kb_dir / "CURRENT").write_text("store-old", encoding="utf-8")
    add_olcf_docs(env["payload"], env["data_dir"])
    await boot(engine)

    assert not (kb_dir / "CURRENT").exists()
    async with AsyncSession(engine) as session:
        kb = await session.get(KnowledgeBaseTable, OLCF_DOCS_KB_ID)
    assert kb is not None and kb.rag_db_path == str(kb_dir / "rag_db")


async def test_no_client_seeds_the_project_without_a_kb(
    env, engine, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "vista_data_payload_dir", None)
    with caplog.at_level(logging.WARNING):
        await boot(engine)

    project = await the_project(engine)
    assert project is not None and project.knowledge_bases == []
    assert await kb_slugs(engine) == []
    assert "OLCF docs corpus unavailable" in caplog.text


async def test_payload_without_olcf_docs_warns_and_continues(env, engine, caplog):
    (env["payload"] / "README.md").write_text("no corpora", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        await boot(engine)
    project = await the_project(engine)
    assert project is not None and project.knowledge_bases == []
    assert "no olcf-docs/ folder" in caplog.text


async def test_a_payload_with_only_olcf_docs_seeds(env, engine, no_indexing):
    add_olcf_docs(env["payload"], env["data_dir"])
    assert [p.name for p in env["payload"].iterdir()] == ["olcf-docs"]
    await boot(engine)

    project = await the_project(engine)
    assert project is not None and project.knowledge_bases == ["olcf-docs"]
    assert await kb_slugs(engine) == ["olcf-docs"]


class _BrokenGitlab:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def has(self, repo_path):
        request = httpx.Request("GET", "https://code.ornl.gov/x")
        raise httpx.HTTPStatusError(
            "502", request=request, response=httpx.Response(502, request=request)
        )


async def test_unreachable_vista_data_does_not_block_startup(
    env, engine, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "vista_data_payload_dir", None)
    monkeypatch.setattr(settings, "vista_data_token", "glpat-stub")
    monkeypatch.setattr(seed_module, "GitlabRepoClient", _BrokenGitlab)
    with caplog.at_level(logging.WARNING):
        await boot(engine)

    project = await the_project(engine)
    assert project is not None and project.knowledge_bases == []
    assert await kb_slugs(engine) == []
    assert "next startup will try again" in caplog.text


# --------------------------------------------------------------------------
# The token path installs a fetched store
# --------------------------------------------------------------------------


def _token_with(monkeypatch, env, repo):
    """A token whose vista-data is the folder `repo`."""
    monkeypatch.setattr(settings, "vista_data_payload_dir", None)
    monkeypatch.setattr(settings, "vista_data_token", "glpat-stub")
    monkeypatch.setattr(
        seed_module,
        "GitlabRepoClient",
        lambda domain, repo_name, token=None: LocalRepoClient(repo),
    )


def _vista_data_folder(root, chunks: int | None = 3):
    """A vista-data `olcf-docs/` as fetched: sections, SOURCE and `rag_db/`."""
    folder = root / "olcf-docs"
    folder.mkdir(parents=True)
    (folder / "systems__frontier__queues.md").write_text("# Queues\n", encoding="utf-8")
    (folder / "SOURCE").write_text("abc123\n", encoding="utf-8")
    if chunks is not None:
        make_vector_store(folder / "rag_db", chunks)
        (folder / "rag_db" / "bm25_corpus.json").write_text("[]", encoding="utf-8")
    return root


async def test_a_token_fetched_rag_db_is_installed_without_indexing(
    env, engine, monkeypatch, no_indexing
):
    _token_with(monkeypatch, env, _vista_data_folder(env["tmp"] / "vista-data"))
    await boot(engine)

    kb_dir = env["data_dir"] / "knowledge-bases/olcf-docs"
    assert (kb_dir / "rag_db/chroma.sqlite3").is_file()
    assert (kb_dir / "rag_db/bm25_corpus.json").is_file()
    # Not among the documents, and the documents are the folder's other files.
    assert not (kb_dir / "pdfs/rag_db").exists()
    assert sorted(p.name for p in (kb_dir / "pdfs").iterdir()) == [
        "SOURCE",
        "systems__frontier__queues.md",
    ]
    project = await the_project(engine)
    assert project is not None and project.knowledge_bases == ["olcf-docs"]


async def test_a_token_fetched_folder_without_rag_db_is_not_seeded(
    env, engine, monkeypatch, no_indexing, caplog
):
    _token_with(
        monkeypatch, env, _vista_data_folder(env["tmp"] / "vista-data", chunks=None)
    )
    with caplog.at_level(logging.WARNING):
        await boot(engine)

    project = await the_project(engine)
    assert project is not None and project.knowledge_bases == []
    assert await kb_slugs(engine) == []
    assert "no rag_db/" in caplog.text


async def test_a_download_cut_short_leaves_no_store_and_resumes(
    env, engine, monkeypatch, no_indexing
):
    repo = _vista_data_folder(env["tmp"] / "vista-data")
    _token_with(monkeypatch, env, repo)
    real = LocalRepoClient.download_file
    calls = {"n": 0}

    async def flaky(self, repo_path, dest):
        calls["n"] += 1
        if repo_path.endswith("bm25_corpus.json") and calls["n"] < 50:
            request = httpx.Request("GET", "https://code.ornl.gov/x")
            raise httpx.ConnectError("cut", request=request)
        await real(self, repo_path, dest)

    monkeypatch.setattr(LocalRepoClient, "download_file", flaky)
    await boot(engine)

    kb_dir = env["data_dir"] / "knowledge-bases/olcf-docs"
    assert not (kb_dir / "rag_db").exists()
    assert await kb_slugs(engine) == []
    project = await the_project(engine)
    assert project is not None and project.knowledge_bases == []

    monkeypatch.setattr(LocalRepoClient, "download_file", real)
    await sync_default_projects(engine)

    assert (kb_dir / "rag_db/chroma.sqlite3").is_file()
    assert not (kb_dir / "pdfs/rag_db").exists()
    project = await the_project(engine)
    assert project is not None and project.knowledge_bases == ["olcf-docs"]


# --------------------------------------------------------------------------
# A bad bundled index is fatal, and a payload never indexes
# --------------------------------------------------------------------------


async def test_empty_bundled_index_raises(env, engine, no_indexing):
    add_olcf_docs(env["payload"], env["data_dir"], chunks=0)
    with pytest.raises(RuntimeError, match="olcf-docs.*empty vector store"):
        await boot(engine)
    assert await kb_slugs(engine) == []


async def test_payload_with_docs_but_no_index_raises_without_indexing(
    env, engine, no_indexing
):
    add_olcf_docs(env["payload"], env["data_dir"], chunks=None)
    with pytest.raises(RuntimeError, match="olcf-docs.*no vector store"):
        await boot(engine)


async def test_science_payload_with_pdfs_but_no_index_raises_without_indexing(
    env, engine, no_indexing
):
    make_payload(env["payload"])  # molten-salt-papers PDFs, no store
    with pytest.raises(RuntimeError, match="molten-salt-papers.*no vector store"):
        await seed_db(engine)


# --------------------------------------------------------------------------
# Upgrade and idempotency
# --------------------------------------------------------------------------


async def test_upgrade_keeps_ai_safety_unchanged_and_adds_hello_olcf(
    env, engine, no_indexing
):
    await add_old_ai_safety(engine, env["data_dir"])
    before = await ai_safety_rows(engine)

    add_olcf_docs(env["payload"], env["data_dir"])
    await boot(engine)

    assert await ai_safety_rows(engine) == before
    project = await the_project(engine)
    assert project is not None and project.knowledge_bases == ["olcf-docs"]
    assert await kb_slugs(engine) == ["ai-safety", "olcf-docs"]


async def test_upgrade_leaves_science_rows_alone(env, engine, no_indexing):
    make_payload(env["payload"])
    make_vector_store(env["data_dir"] / "knowledge-bases/molten-salt-papers/rag_db")
    await seed_db(engine)  # a database as seeded before this change

    async def science_rows():
        async with AsyncSession(engine) as session:
            projects = (
                await session.exec(
                    select(ProjectTable).where(ProjectTable.id != HELLO_OLCF_PROJECT_ID)
                )
            ).all()
            kbs = (await session.exec(select(KnowledgeBaseTable))).all()
            return sorted((p.model_dump_json() for p in projects)), sorted(
                k.model_dump_json() for k in kbs
            )

    before = await science_rows()
    assert await the_project(engine) is None

    add_olcf_docs(env["payload"], env["data_dir"])
    await sync_default_projects(engine)

    project = await the_project(engine)
    assert project is not None and project.knowledge_bases == ["olcf-docs"]
    assert await kb_slugs(engine) == ["molten-salt-papers", "olcf-docs"]
    after = await science_rows()
    assert after[0] == before[0]
    assert set(before[1]) <= set(after[1])


async def test_an_edited_system_prompt_survives_restart(env, engine, no_indexing):
    add_olcf_docs(env["payload"], env["data_dir"])
    await boot(engine)
    async with AsyncSession(engine) as session:
        project = await session.get(ProjectTable, HELLO_OLCF_PROJECT_ID)
        project.system_prompt = "my own prompt"
        session.add(project)
        await session.commit()

    await sync_default_projects(engine)

    project = await the_project(engine)
    assert project is not None and project.system_prompt == "my own prompt"


async def test_a_user_attached_kb_is_kept(env, engine, no_indexing):
    add_olcf_docs(env["payload"], env["data_dir"])
    await boot(engine)
    async with AsyncSession(engine) as session:
        project = await session.get(ProjectTable, HELLO_OLCF_PROJECT_ID)
        project.knowledge_bases = ["olcf-docs", "my-papers"]
        session.add(project)
        await session.commit()

    await sync_default_projects(engine)

    project = await the_project(engine)
    assert project is not None
    assert project.knowledge_bases == ["olcf-docs", "my-papers"]


async def test_a_kb_available_on_a_later_boot_is_created_and_attached(
    env, engine, no_indexing
):
    await boot(engine)  # payload without olcf-docs/
    project = await the_project(engine)
    assert project is not None and project.knowledge_bases == []
    async with AsyncSession(engine) as session:
        project = await session.get(ProjectTable, HELLO_OLCF_PROJECT_ID)
        project.description = "edited"
        session.add(project)
        await session.commit()

    add_olcf_docs(env["payload"], env["data_dir"])
    await sync_default_projects(engine)

    project = await the_project(engine)
    assert project is not None
    assert project.knowledge_bases == ["olcf-docs"]
    assert project.description == "edited"
    assert await kb_slugs(engine) == ["olcf-docs"]


async def test_sync_is_idempotent(env, engine, no_indexing):
    add_olcf_docs(env["payload"], env["data_dir"])
    await boot(engine)
    await sync_default_projects(engine)
    await sync_default_projects(engine)
    project = await the_project(engine)
    assert project is not None and project.knowledge_bases == ["olcf-docs"]
    assert await kb_slugs(engine) == ["olcf-docs"]


async def test_a_deleted_hello_olcf_is_restored_with_its_kb(env, engine, no_indexing):
    add_olcf_docs(env["payload"], env["data_dir"])
    await boot(engine)
    async with AsyncSession(engine) as session:
        await session.delete(await session.get(ProjectTable, HELLO_OLCF_PROJECT_ID))
        await session.commit()
    assert await the_project(engine) is None

    await sync_default_projects(engine)

    project = await the_project(engine)
    assert project is not None and project.knowledge_bases == ["olcf-docs"]
    assert project.skills == SKILLS
    assert project.usage_limits.get("request_limit") == 50


async def test_a_deleted_ai_safety_project_is_not_recreated(env, engine, no_indexing):
    await add_old_ai_safety(engine, env["data_dir"])
    add_olcf_docs(env["payload"], env["data_dir"])
    await boot(engine)
    async with AsyncSession(engine) as session:
        await session.delete(await session.get(ProjectTable, AI_SAFETY_PROJECT_ID))
        await session.commit()

    await sync_default_projects(engine)
    await sync_default_projects(engine)

    async with AsyncSession(engine) as session:
        assert await session.get(ProjectTable, AI_SAFETY_PROJECT_ID) is None
        names = [p.name for p in (await session.exec(select(ProjectTable))).all()]
    assert names == ["hello-olcf"]


async def test_science_projects_are_not_added_to_an_existing_database(
    env, engine, monkeypatch, no_indexing
):
    add_olcf_docs(env["payload"], env["data_dir"])
    await boot(engine)  # an existing database, without science
    monkeypatch.setattr(settings, "vista_data_payload_dir", None)
    monkeypatch.setattr(settings, "vista_data_token", "glpat-stub")
    monkeypatch.setattr(settings, "seed_science_projects", True)
    monkeypatch.setattr(
        seed_module,
        "GitlabRepoClient",
        lambda domain, repo, token=None: LocalRepoClient(env["payload"]),
    )

    await boot(engine)

    async with AsyncSession(engine) as session:
        names = [p.name for p in (await session.exec(select(ProjectTable))).all()]
    assert names == ["hello-olcf"]


# --------------------------------------------------------------------------
# seed_item_id covers every document the knowledge base accepts
# --------------------------------------------------------------------------


def _old_seed_item_id(kb_id, name, description, pdfs_dir):
    """`seed_item_id` as it was when it hashed only PDFs."""
    import json
    import uuid

    pdfs = sorted(
        (path.relative_to(pdfs_dir).as_posix(), path.stat().st_size)
        for path in pdfs_dir.rglob("*")
        if path.suffix.lower() == ".pdf" and path.is_file() and not path.is_symlink()
    )
    key = json.dumps([str(kb_id), name, description, pdfs])
    return uuid.uuid5(seed_module.SEED_ITEM_NAMESPACE, key)


def test_seed_item_id_changes_when_a_markdown_file_changes(tmp_path):
    folder = tmp_path / "pdfs"
    folder.mkdir()
    (folder / "a__b.md").write_text("one", encoding="utf-8")
    (folder / "c__d.md").write_text("two", encoding="utf-8")
    before = seed_module.seed_item_id(OLCF_DOCS_KB_ID, "n", "d", folder)
    assert seed_module.seed_item_id(OLCF_DOCS_KB_ID, "n", "d", folder) == before
    (folder / "c__d.md").write_text("two, longer", encoding="utf-8")
    assert seed_module.seed_item_id(OLCF_DOCS_KB_ID, "n", "d", folder) != before


def test_a_pdf_only_seed_item_id_is_what_it_was(tmp_path):
    folder = tmp_path / "pdfs"
    (folder / "nested").mkdir(parents=True)
    (folder / "one.pdf").write_bytes(b"%PDF-1.4 one")
    (folder / "nested" / "two.PDF").write_bytes(b"%PDF-1.4 two")
    assert seed_module.seed_item_id(
        OLCF_DOCS_KB_ID, "n", "d", folder
    ) == _old_seed_item_id(OLCF_DOCS_KB_ID, "n", "d", folder)


def test_source_and_a_stray_rag_db_do_not_affect_the_seed_item_id(tmp_path):
    folder = tmp_path / "pdfs"
    folder.mkdir()
    (folder / "a__b.md").write_text("one", encoding="utf-8")
    before = seed_module.seed_item_id(OLCF_DOCS_KB_ID, "n", "d", folder)
    (folder / "SOURCE").write_text("abc123\n", encoding="utf-8")
    (folder / "rag_db" / "uuid-dir").mkdir(parents=True)
    (folder / "rag_db" / "chroma.sqlite3").write_bytes(b"x")
    (folder / "rag_db" / "bm25_corpus.json").write_text("[]", encoding="utf-8")
    (folder / "rag_db" / "uuid-dir" / "stray.md").write_text("s", encoding="utf-8")
    assert seed_module.seed_item_id(OLCF_DOCS_KB_ID, "n", "d", folder) == before
