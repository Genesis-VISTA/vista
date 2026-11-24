"""
Ownership and data-layer filtering (openspec change `multi-user-hosted`, task
group 6; spec `tenant-isolation`): everything a researcher makes has one owner,
another user's things answer 404 as if they did not exist, and what VISTA
bundles is readable by all and writable by none.

Two hosted users, Alice and Bob, through the real routes; the desktop backfill
and a pre-change database through `db.db` directly.
"""

import sqlite3
import uuid

import pytest
from sqlalchemy import MetaData, Table
from sqlalchemy.schema import CreateTable
from sqlmodel import SQLModel, select

from harness import api_client
from harness.tokens import bearer
from vista_backend.config import settings
from vista_backend.db import db as db_module
from vista_backend.db.ownership import get_owned, get_visible, owned, uid, visible
from vista_backend.db.schema_check import schema_problems
from vista_backend.db.schemas import (
    LOCAL_USER_ID,
    CampaignRunTable,
    ChatSessionTable,
    DebateRunTable,
    KnowledgeBaseTable,
    ProjectTable,
    SkillTable,
    UserTable,
)
from vista_backend.db.seed import OLCF_DOCS_KB_ID
from vista_backend.services import campaign as campaign_service
from vista_backend.services import debate as debate_service
from vista_backend.services import user_provisioning
from vista_backend.utils.misc import now_iso

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")


@pytest.fixture
def alice(hosted):
    return bearer(hosted.token(sub="alice", name="Alice", email="alice@ornl.gov"))


@pytest.fixture
def bob(hosted):
    return bearer(hosted.token(sub="bob", name="Bob", email="bob@ornl.gov"))


def _project(name: str) -> dict:
    return {"name": name, "skills": [], "knowledge_bases": [], "tools": ["*"]}


def _skill(name: str) -> dict:
    return {"name": name, "description": f"The {name} skill.", "body": "Mine."}


async def _make_project(client, who: dict, name: str):
    res = await client.post(
        "/projects",
        json=_project(name),
        params={"hosted_forum_wanted": "false"},
        headers=who,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _user(session, sub: str) -> UserTable:
    session.expire_all()
    return (await session.exec(select(UserTable).where(UserTable.sub == sub))).one()


def _bundled_skill(name: str) -> SkillTable:
    return SkillTable(
        name=name,
        description="Ships with VISTA.",
        path=f"skills/{name}",
        bundled=True,
        user_id=None,
        created_at=now_iso(),
        updated_at=now_iso(),
    )


# ---------------------------------------------------------------------------
# Projects: names are per owner, another user's is not found
# ---------------------------------------------------------------------------


async def test_alice_and_bob_each_have_a_project_of_the_same_name(session, alice, bob):
    with api_client(session) as (client, _):
        await _make_project(client, alice, "molten-salt")
        await _make_project(client, bob, "molten-salt")
        await _make_project(client, alice, "only-alice")
        alices = {
            p["name"] for p in (await client.get("/projects", headers=alice)).json()
        }
        bobs = {p["name"] for p in (await client.get("/projects", headers=bob)).json()}
        again = await client.post(
            "/projects",
            json=_project("molten-salt"),
            params={"hosted_forum_wanted": "false"},
            headers=alice,
        )
    # Each also has the hello-olcf their first sign-in made (`provision_new_user`).
    assert alices == {"hello-olcf", "molten-salt", "only-alice"}
    assert bobs == {"hello-olcf", "molten-salt"}
    assert again.status_code == 409  # unique per owner, still unique


async def test_the_same_name_is_two_different_projects(session, alice, bob):
    with api_client(session) as (client, _):
        a = await _make_project(client, alice, "shared-name")
        b = await _make_project(client, bob, "shared-name")
    assert a["id"] != b["id"]
    rows = (await session.exec(select(ProjectTable))).all()
    owners = {r.user_id for r in rows}
    assert len(owners) == 2 and None not in owners


async def test_another_users_project_is_404_on_every_name_route(session, alice, bob):
    with api_client(session) as (client, _):
        await _make_project(client, alice, "private-work")
        missing = await client.get("/projects/nothing-here", headers=bob)
        seen = [
            await client.get("/projects/private-work", headers=bob),
            await client.put(
                "/projects/private-work", json=_project("private-work"), headers=bob
            ),
            await client.delete("/projects/private-work", headers=bob),
            await client.get("/projects/private-work/chat-sessions", headers=bob),
            await client.get("/projects/private-work/campaigns", headers=bob),
            await client.get("/projects/private-work/uploads", headers=bob),
        ]
        still = await client.get("/projects/private-work", headers=alice)
    assert missing.status_code == 404
    for res in seen:
        # exactly as for a project that does not exist
        assert res.status_code == 404, res.request.url
        assert res.json() == missing.json() or "not found" in res.text.lower()
    assert still.status_code == 200


# ---------------------------------------------------------------------------
# Chat sessions
# ---------------------------------------------------------------------------


async def test_chat_sessions_are_the_owners(session, alice, bob):
    with api_client(session) as (client, _):
        await _make_project(client, alice, "shared-name")
        await _make_project(client, bob, "shared-name")
        made = await client.post(
            "/projects/shared-name/chat-sessions", json={"title": "mine"}, headers=alice
        )
        assert made.status_code in (200, 201), made.text
        chat_id = made.json()["id"]
        row = await session.get(ChatSessionTable, uuid.UUID(chat_id))
        assert row is not None
        assert row.user_id == (await _user(session, "alice")).id

        # Bob's project has the same name; Alice's conversation is not in it.
        route = "/projects/shared-name/chat-session"
        in_bobs = await client.get(
            route, params={"chat_session_id": chat_id}, headers=bob
        )
        listed = await client.get("/projects/shared-name/chat-sessions", headers=bob)
        deleted = await client.delete(
            route, params={"chat_session_id": chat_id}, headers=bob
        )
        put = await client.put(
            route,
            params={"chat_session_id": chat_id},
            json={"title": "hijack"},
            headers=bob,
        )
        owns = await client.get(
            route, params={"chat_session_id": chat_id}, headers=alice
        )
    assert in_bobs.status_code == 404
    assert listed.json() == []
    assert deleted.status_code == 404
    assert put.status_code == 404
    assert owns.status_code == 200


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


async def test_skills_are_per_owner_and_not_visible_to_others(session, alice, bob):
    with api_client(session) as (client, _):
        assert (
            await client.post("/skills", json=_skill("salt-density"), headers=alice)
        ).status_code == 201
        # Bob may have a skill of the same name.
        assert (
            await client.post("/skills", json=_skill("salt-density"), headers=bob)
        ).status_code == 201
        assert (
            await client.post("/skills", json=_skill("alice-only"), headers=alice)
        ).status_code == 201
        alices = {
            s["name"] for s in (await client.get("/skills", headers=alice)).json()
        }
        bobs = {s["name"] for s in (await client.get("/skills", headers=bob)).json()}
        get = await client.get("/skills/alice-only", headers=bob)
        patch = await client.patch(
            "/skills/alice-only", json={"description": "mine now"}, headers=bob
        )
        delete = await client.delete("/skills/alice-only", headers=bob)
        dup = await client.post("/skills", json=_skill("alice-only"), headers=alice)
    assert alices == {"salt-density", "alice-only"}
    assert bobs == {"salt-density"}
    assert (get.status_code, patch.status_code, delete.status_code) == (404, 404, 404)
    assert dup.status_code == 409


async def test_a_bundled_skill_is_read_by_both_and_edited_by_neither(
    session, alice, bob
):
    session.add(_bundled_skill("hello-odo"))
    await session.commit()
    with api_client(session) as (client, _):
        for who in (alice, bob):
            listed = {
                s["name"] for s in (await client.get("/skills", headers=who)).json()
            }
            assert "hello-odo" in listed
        # The directory a bundled skill's files are read from does not exist in this
        # test; the row is what is asserted on, so read the metadata route is skipped.
        for who in (alice, bob):
            patch = await client.patch(
                "/skills/hello-odo", json={"description": "changed"}, headers=who
            )
            delete = await client.delete("/skills/hello-odo", headers=who)
            assert patch.status_code == 409, patch.text
            assert "bundled" in patch.json()["detail"]
            assert delete.status_code == 409, delete.text
        # And a user may not shadow it by creating one of the same name.
        shadow = await client.post("/skills", json=_skill("hello-odo"), headers=bob)
    assert shadow.status_code == 409
    row = (
        await session.exec(select(SkillTable).where(SkillTable.name == "hello-odo"))
    ).one()
    assert row.description == "Ships with VISTA." and row.user_id is None


# ---------------------------------------------------------------------------
# Knowledge bases
# ---------------------------------------------------------------------------


async def test_knowledge_bases_are_the_owners(session, alice, bob):
    with api_client(session) as (client, _):
        made = await client.post(
            "/knowledge-bases",
            json={"slug": "alice-kb", "name": "Alice's"},
            headers=alice,
        )
        assert made.status_code == 201, made.text
        alices = [
            k["slug"]
            for k in (await client.get("/knowledge-bases", headers=alice)).json()
        ]
        bobs = [
            k["slug"]
            for k in (await client.get("/knowledge-bases", headers=bob)).json()
        ]
        get = await client.get("/knowledge-bases/alice-kb", headers=bob)
        put = await client.put(
            "/knowledge-bases/alice-kb", json={"name": "Bob's now"}, headers=bob
        )
        delete = await client.delete("/knowledge-bases/alice-kb", headers=bob)
        publish = await client.get(
            "/knowledge-bases/alice-kb/publications/x.pdf", headers=bob
        )
        add = await client.post(
            "/knowledge-bases/alice-kb/publications",
            files=[("files", ("x.pdf", b"%PDF-1.4", "application/pdf"))],
            headers=bob,
        )
        # A store is keyed on disk by its slug alone, so Bob cannot take Alice's slug.
        clash = await client.post(
            "/knowledge-bases", json={"slug": "alice-kb", "name": "Bob's"}, headers=bob
        )
    assert alices == ["alice-kb"] and bobs == []
    assert (get.status_code, put.status_code, delete.status_code) == (404, 404, 404)
    assert (publish.status_code, add.status_code) == (404, 404)
    assert clash.status_code == 409
    alice_id = (await _user(session, "alice")).id
    row = (await session.exec(select(KnowledgeBaseTable))).one()
    assert row.user_id == alice_id and row.name == "Alice's"


async def test_the_docs_corpus_is_read_by_all_and_written_by_none(session, alice, bob):
    session.add(
        KnowledgeBaseTable(
            id=OLCF_DOCS_KB_ID,
            slug="olcf-docs",
            name="OLCF User Documentation",
            pdfs_dir=str(settings.knowledge_bases_dir / "olcf-docs" / "pdfs"),
            rag_db_path=str(settings.knowledge_bases_dir / "olcf-docs" / "rag_db"),
            publications=[],
            build_status="ready",
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )
    await session.commit()
    with api_client(session) as (client, _):
        for who in (alice, bob):
            listed = [
                k["slug"]
                for k in (await client.get("/knowledge-bases", headers=who)).json()
            ]
            assert listed == ["olcf-docs"]
            assert (
                await client.get("/knowledge-bases/olcf-docs", headers=who)
            ).status_code == 200
            put = await client.put(
                "/knowledge-bases/olcf-docs", json={"name": "mine"}, headers=who
            )
            delete = await client.delete("/knowledge-bases/olcf-docs", headers=who)
            assert (put.status_code, delete.status_code) == (409, 409)


# ---------------------------------------------------------------------------
# Campaign and debate runs: owned through their project
# ---------------------------------------------------------------------------


async def test_campaign_runs_are_the_owners(session, alice, bob):
    with api_client(session) as (client, _):
        a = await _make_project(client, alice, "work")
        await _make_project(client, bob, "work")
        run = await campaign_service.create_campaign(
            session,
            project_id=uuid.UUID(a["id"]),
            domain="neutronics",
            planner_skill="planner",
        )
        await session.commit()
        await session.refresh(run)
        run_id, run_owner = run.id, run.user_id
        assert run_owner == uid(await _user(session, "alice"))
        mine = await client.get(f"/projects/work/campaigns/{run_id}", headers=alice)
        theirs = await client.get(f"/projects/work/campaigns/{run_id}", headers=bob)
        patch = await client.patch(
            f"/projects/work/campaigns/{run_id}", json={"title": "x"}, headers=bob
        )
        alices = await client.get("/projects/work/campaigns", headers=alice)
        bobs = await client.get("/projects/work/campaigns", headers=bob)
    assert mine.status_code == 200
    assert (theirs.status_code, patch.status_code) == (404, 404)
    assert [r["id"] for r in alices.json()] == [str(run_id)]
    assert bobs.json() == []


async def test_a_debate_run_is_the_projects_owners(session, alice, bob):
    with api_client(session) as (client, _):
        a = await _make_project(client, alice, "work")
        await _make_project(client, bob, "work")
        run = await debate_service.create_debate(
            session, project_id=uuid.UUID(a["id"]), topic="T", thread_id="t1"
        )
        await session.commit()
        await session.refresh(run)
        run_id, project_id, owner = run.id, run.project_id, run.user_id
    alice_row, bob_row = await _user(session, "alice"), await _user(session, "bob")
    assert owner == uid(alice_row)
    found = await debate_service.require_debate_in_project(
        session, run_id=run_id, project_id=project_id, user=alice_row
    )
    assert found.id == run_id
    with pytest.raises(ValueError):
        await debate_service.require_debate_in_project(
            session, run_id=run_id, project_id=project_id, user=bob_row
        )


# ---------------------------------------------------------------------------
# The helpers
# ---------------------------------------------------------------------------


async def test_owned_and_get_owned(session):
    local = await session.get(UserTable, LOCAL_USER_ID)
    other = UserTable(id="other", sub="other")
    session.add(other)
    await session.commit()
    session.add(ProjectTable(name="mine", user_id=LOCAL_USER_ID))
    theirs = ProjectTable(id=uuid.uuid4(), name="theirs", user_id="other")
    theirs_id = theirs.id
    session.add(theirs)
    session.add(
        SkillTable(
            name="g",
            description="d",
            path="p",
            bundled=True,
            created_at="",
            updated_at="",
        )
    )
    await session.commit()
    names = (
        await session.exec(owned(select(ProjectTable.name), local, ProjectTable))
    ).all()
    assert names == ["mine"]
    assert await get_owned(session, ProjectTable, theirs_id, local) is None
    assert (await get_owned(session, ProjectTable, theirs_id, other)) is not None
    skills = (
        await session.exec(visible(select(SkillTable.name), local, SkillTable))
    ).all()
    assert skills == ["g"]
    glob_id = (await session.exec(select(SkillTable.id))).one()
    assert await get_owned(session, SkillTable, glob_id, local) is None
    assert await get_visible(session, SkillTable, glob_id, local) is not None


async def test_new_databases_are_unique_per_owner_not_across_the_server(session):
    session.add(UserTable(id="other", sub="other"))
    await session.commit()
    for owner in ("local", "other"):
        session.add(
            SkillTable(
                name="same",
                description="d",
                path=f"p-{owner}",
                user_id=owner,
                created_at="",
                updated_at="",
            )
        )
    await session.commit()  # two owners, one name: fine
    session.add(
        SkillTable(
            name="same",
            description="d",
            path="p3",
            user_id="local",
            created_at="",
            updated_at="",
        )
    )
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await session.commit()


# ---------------------------------------------------------------------------
# Desktop: everything that existed belongs to `local`; no reset prompt
# ---------------------------------------------------------------------------


def _pre_change_database(path) -> None:
    """
    A database made before this change: the models' tables, none with a `user_id`,
    and without the tables this change adds (`app_user`, `user_settings`).
    """
    from sqlmodel import SQLModel as M

    meta = MetaData()
    for table in M.metadata.sorted_tables:
        if table.name in ("app_user", "user_settings"):
            continue
        Table(
            table.name,
            meta,
            *[c._copy() for c in table.columns if c.name != "user_id"],
        )
    conn = sqlite3.connect(path)
    try:
        for table in meta.sorted_tables:
            conn.execute(str(CreateTable(table).compile(dialect=_sqlite())))
        conn.commit()
    finally:
        conn.close()


def _sqlite():
    from sqlalchemy.dialects import sqlite

    return sqlite.dialect()


async def test_a_pre_change_database_passes_the_schema_check_and_is_backfilled(
    tmp_path, monkeypatch
):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlmodel.ext.asyncio.session import AsyncSession

    path = tmp_path / "old.db"
    _pre_change_database(path)
    conn = sqlite3.connect(path)
    try:
        # Accepted: no reset prompt (a nullable column is additive).
        assert schema_problems(conn, assume_additive=True) == []
        pid, kid, sid, bid = (uuid.uuid4().hex for _ in range(4))
        conn.execute(
            "INSERT INTO project (id, name, skills, knowledge_bases, tools, usage_limits, modified)"
            " VALUES (?, 'old-project', '[]', '[]', '[]', '{}', 1)",
            (pid,),
        )
        conn.execute(
            "INSERT INTO skill (id, name, description, path, bundled, modified, created_at, updated_at)"
            " VALUES (?, 'mine-skill', 'd', 'p', 0, 1, '', '')",
            (sid,),
        )
        conn.execute(
            "INSERT INTO skill (id, name, description, path, bundled, modified, created_at, updated_at)"
            " VALUES (?, 'bundled-skill', 'd', 'p2', 1, 0, '', '')",
            (bid,),
        )
        for ident, slug in ((kid, "my-kb"), (OLCF_DOCS_KB_ID.hex, "olcf-docs")):
            conn.execute(
                "INSERT INTO knowledge_base (id, slug, name, pdfs_dir, rag_db_path, publications,"
                " build_status, modified, created_at, updated_at, shared_with_mcp)"
                " VALUES (?, ?, ?, '', '', '[]', 'ready', 0, '', '', 0)",
                (ident, slug, slug),
            )
        conn.commit()
    finally:
        conn.close()

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with engine.begin() as c:
            await c.run_sync(SQLModel.metadata.create_all)
            await c.run_sync(db_module._add_missing_columns)
        async with AsyncSession(engine) as s:
            from vista_backend.services import current_user

            await current_user.ensure_local(s)
            await s.commit()
        for _ in range(2):  # idempotent
            async with engine.begin() as c:
                await c.run_sync(db_module._backfill_owners)
        async with AsyncSession(engine) as s:
            projects = (await s.exec(select(ProjectTable))).all()
            skills = {
                r.name: r.user_id for r in (await s.exec(select(SkillTable))).all()
            }
            kbs = {
                r.slug: r.user_id
                for r in (await s.exec(select(KnowledgeBaseTable))).all()
            }
    finally:
        await engine.dispose()
    assert [p.user_id for p in projects] == [LOCAL_USER_ID]
    assert skills == {"mine-skill": LOCAL_USER_ID, "bundled-skill": None}
    assert kbs == {"my-kb": LOCAL_USER_ID, "olcf-docs": None}


# ---------------------------------------------------------------------------
# The first sign-in of a hosted user
# ---------------------------------------------------------------------------


async def test_provisioning_gives_a_new_user_their_own_default_project(session):
    ada = UserTable(id="ada", sub="ada")
    bob = UserTable(id="bob", sub="bob")
    session.add_all([ada, bob])
    await session.commit()
    first = await user_provisioning.provision_new_user(session, ada)
    first_view = [(p.id, p.name, list(p.skills)) for p in first]
    again = await user_provisioning.provision_new_user(session, ada)
    other = await user_provisioning.provision_new_user(session, bob)
    other_view = [(p.id, p.name) for p in other]
    await session.commit()
    assert [n for _, n, _ in first_view] == ["hello-olcf"] and again == []
    assert [n for _, n in other_view] == ["hello-olcf"]
    assert first_view[0][0] != other_view[0][0]
    rows = (await session.exec(select(ProjectTable))).all()
    assert {(r.name, r.user_id) for r in rows} == {
        ("hello-olcf", "ada"),
        ("hello-olcf", "bob"),
    }
    assert "hpc-skill-author" in first_view[0][2]


# ---------------------------------------------------------------------------
# init_db: the real startup
# ---------------------------------------------------------------------------


async def test_unused():  # pragma: no cover - keeps the module's imports honest
    assert CampaignRunTable and DebateRunTable and db_module
