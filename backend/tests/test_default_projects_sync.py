"""
`sync_default_projects`: the AI-safety default project and knowledge base.

Hermetic. Payload tests read a vista-data tree in `tmp_path` with a prebuilt
Chroma store, so nothing is embedded; the token path is covered by faking the
GitLab client, never by reaching it.
"""

import logging

import httpx
import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.config import settings
from vista_backend.db import seed as seed_module
from vista_backend.db.schemas import (
    KnowledgeBaseTable,
    ProjectMemberTable,
    ProjectTable,
)
from vista_backend.db.seed import (
    AI_SAFETY_KB_ID,
    AI_SAFETY_PROJECT_ID,
    LocalRepoClient,
    seed_db,
    sync_default_projects,
)
from vista_backend.utils import indexer

from test_seed_payload import make_engine, make_payload, make_vector_store

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


def add_ai_safety(payload, data_dir, chunks: int | None = 3):
    """Add `ai-safety/` to a payload and, unless `chunks` is None, its prebuilt index."""
    pdf = payload / "ai-safety" / "roadmap.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-1.4 roadmap")
    if chunks is not None:
        make_vector_store(data_dir / "knowledge-bases" / "ai-safety" / "rag_db", chunks)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A payload dir (AI-safety only unless extended) wired into settings."""
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
    await seed_db(engine)
    await sync_default_projects(engine)


async def the_project(engine) -> ProjectTable | None:
    async with AsyncSession(engine) as session:
        return await session.get(ProjectTable, AI_SAFETY_PROJECT_ID)


async def kb_slugs(engine) -> list[str]:
    async with AsyncSession(engine) as session:
        return sorted(
            k.slug for k in (await session.exec(select(KnowledgeBaseTable))).all()
        )


# --------------------------------------------------------------------------
# First run
# --------------------------------------------------------------------------


async def test_fresh_db_gets_the_project_with_an_attached_indexed_kb(
    env, engine, no_indexing
):
    add_ai_safety(env["payload"], env["data_dir"])
    await boot(engine)

    project = await the_project(engine)
    assert project is not None
    assert project.name == "ai-safety-autonomous-labs"
    assert project.knowledge_bases == ["ai-safety"]
    assert project.skills == []
    assert project.tools == ["*", "!agenthpc_*"]
    assert project.usage_limits.get("request_limit") == 50
    assert "AI Safety in Autonomous Labs" in project.system_prompt

    async with AsyncSession(engine) as session:
        kb = await session.get(KnowledgeBaseTable, AI_SAFETY_KB_ID)
        assert kb is not None
        assert (kb.slug, kb.name, kb.build_status) == (
            "ai-safety",
            "AI Safety Papers",
            "ready",
        )
        members = (await session.exec(select(ProjectMemberTable))).all()
        assert [m.project_id for m in members] == [AI_SAFETY_PROJECT_ID]
    # The PDFs are copied for citation display.
    assert (env["data_dir"] / "knowledge-bases/ai-safety/pdfs/roadmap.pdf").is_file()
    assert await kb_slugs(engine) == ["ai-safety"]


async def test_no_client_seeds_the_project_without_a_kb(
    env, engine, monkeypatch, caplog
):
    monkeypatch.setattr(settings, "vista_data_payload_dir", None)
    with caplog.at_level(logging.WARNING):
        await boot(engine)

    project = await the_project(engine)
    assert project is not None and project.knowledge_bases == []
    assert await kb_slugs(engine) == []
    assert "AI-safety corpus unavailable" in caplog.text


async def test_payload_without_ai_safety_warns_and_continues(env, engine, caplog):
    (env["payload"] / "README.md").write_text("no corpora", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        await boot(engine)
    project = await the_project(engine)
    assert project is not None and project.knowledge_bases == []
    assert "no ai-safety/ folder" in caplog.text


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
# A bad bundled index is fatal, and a payload never indexes
# --------------------------------------------------------------------------


async def test_empty_bundled_index_raises(env, engine, no_indexing):
    add_ai_safety(env["payload"], env["data_dir"], chunks=0)
    with pytest.raises(RuntimeError, match="ai-safety.*empty vector store"):
        await boot(engine)
    assert await kb_slugs(engine) == []


async def test_payload_with_pdfs_but_no_index_raises_without_indexing(
    env, engine, no_indexing
):
    add_ai_safety(env["payload"], env["data_dir"], chunks=None)
    with pytest.raises(RuntimeError, match="ai-safety.*no vector store"):
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


async def test_upgrade_adds_the_project_and_leaves_science_rows_alone(
    env, engine, no_indexing
):
    make_payload(env["payload"])
    make_vector_store(env["data_dir"] / "knowledge-bases/molten-salt-papers/rag_db")
    await seed_db(engine)  # a database as seeded before this change

    async def science_rows():
        async with AsyncSession(engine) as session:
            projects = (
                await session.exec(
                    select(ProjectTable).where(ProjectTable.id != AI_SAFETY_PROJECT_ID)
                )
            ).all()
            kbs = (await session.exec(select(KnowledgeBaseTable))).all()
            return sorted((p.model_dump_json() for p in projects)), sorted(
                k.model_dump_json() for k in kbs
            )

    before = await science_rows()
    assert await the_project(engine) is None

    add_ai_safety(env["payload"], env["data_dir"])
    await sync_default_projects(engine)

    project = await the_project(engine)
    assert project is not None and project.knowledge_bases == ["ai-safety"]
    assert await kb_slugs(engine) == ["ai-safety", "molten-salt-papers"]
    after = await science_rows()
    assert after[0] == before[0]
    # The molten-salt KB row is untouched; only the new KB row was added.
    assert set(before[1]) <= set(after[1])


async def test_an_edited_system_prompt_survives_restart(env, engine, no_indexing):
    add_ai_safety(env["payload"], env["data_dir"])
    await boot(engine)
    async with AsyncSession(engine) as session:
        project = await session.get(ProjectTable, AI_SAFETY_PROJECT_ID)
        project.system_prompt = "my own prompt"
        session.add(project)
        await session.commit()

    await sync_default_projects(engine)

    project = await the_project(engine)
    assert project is not None and project.system_prompt == "my own prompt"


async def test_a_user_attached_kb_is_kept(env, engine, no_indexing):
    add_ai_safety(env["payload"], env["data_dir"])
    await boot(engine)
    async with AsyncSession(engine) as session:
        project = await session.get(ProjectTable, AI_SAFETY_PROJECT_ID)
        project.knowledge_bases = ["ai-safety", "my-papers"]
        session.add(project)
        await session.commit()

    await sync_default_projects(engine)

    project = await the_project(engine)
    assert project is not None
    assert project.knowledge_bases == ["ai-safety", "my-papers"]


async def test_a_kb_available_on_a_later_boot_is_created_and_attached(
    env, engine, no_indexing
):
    await boot(engine)  # payload without ai-safety/
    project = await the_project(engine)
    assert project is not None and project.knowledge_bases == []
    async with AsyncSession(engine) as session:
        project = await session.get(ProjectTable, AI_SAFETY_PROJECT_ID)
        project.description = "edited"
        session.add(project)
        await session.commit()

    add_ai_safety(env["payload"], env["data_dir"])
    await sync_default_projects(engine)

    project = await the_project(engine)
    assert project is not None
    assert project.knowledge_bases == ["ai-safety"]
    assert project.description == "edited"
    assert await kb_slugs(engine) == ["ai-safety"]


async def test_sync_is_idempotent(env, engine, no_indexing):
    add_ai_safety(env["payload"], env["data_dir"])
    await boot(engine)
    await sync_default_projects(engine)
    await sync_default_projects(engine)
    project = await the_project(engine)
    assert project is not None and project.knowledge_bases == ["ai-safety"]
    assert await kb_slugs(engine) == ["ai-safety"]


async def test_a_deleted_project_is_restored_with_its_kb(env, engine, no_indexing):
    add_ai_safety(env["payload"], env["data_dir"])
    await boot(engine)
    async with AsyncSession(engine) as session:
        await session.delete(await session.get(ProjectTable, AI_SAFETY_PROJECT_ID))
        await session.commit()
    assert await the_project(engine) is None

    await sync_default_projects(engine)

    project = await the_project(engine)
    assert project is not None and project.knowledge_bases == ["ai-safety"]
    assert project.usage_limits.get("request_limit") == 50


async def test_science_projects_are_not_added_to_an_existing_database(
    env, engine, monkeypatch, no_indexing
):
    add_ai_safety(env["payload"], env["data_dir"])
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
    assert names == ["ai-safety-autonomous-labs"]
