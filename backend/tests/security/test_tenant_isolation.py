"""
Tenant isolation, route by route (openspec change `multi-user-hosted`, task 13.1;
spec `tenant-isolation`).

Alice owns things; Bob, signed in on the same hosted deployment, reaches for
them through every route the app has and is told they do not exist.

The suite is built from the app, not from a list of what someone remembered:

* `ROUTES` classifies every route of `vista_backend.api.api.app` as `OWNED` (its
  path or body names something a user owns), `USER` (acts on the caller's own
  settings, or lists and creates the caller's own things) or `PUBLIC` (no
  account, no data). `test_every_route_is_classified` fails on a route in no
  table, and on a table row with no route: a new route cannot land without
  someone deciding which it is and writing its case.
* Each `OWNED` route is called by Bob in two worlds. In A he has nothing named as
  Alice's things are: her project, skill, knowledge base, chat, run, thread, task,
  inbox item, Slack link and files are all 404. In B he has a project, skill and
  knowledge base of the same names (his own, different ids), so a route that
  looked up by name alone, or by id without the owner, would answer with Alice's.
  A `child` route names something only Alice has (a chat, a run, a file) and
  must be 404 in B too; the rest may answer with Bob's own, and must not carry a
  byte of Alice's. Every Alice-owned thing holds `CANARY`, which Bob's responses
  are searched for, and her things are read back after each call to be intact.
* Every route that is not public refuses a call with no token, and a bad one (401).
* Lists show Bob his own and the global rows only; a credential in Alice's
  settings reaches her HPC tool metadata and nothing of Bob's (the old
  `test_tenant_isolation`'s canary, kept).

Hermetic: the app is called in-process, no model, no sandbox, no network. A route
that would start an agent or reach a server is called in world A only, where it
is refused before it does either (marked `modes="A"`, with why).
"""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from sqlmodel import select

import vista_backend.agents.agents as agents_module
import vista_backend.metrics as metrics_module
from harness import api_client, make_local_settings, make_project
from harness.tokens import bearer
from vista_backend.agents.agents import ProjectAgent, agent_volume_root
from vista_backend.agents.eval_metrics import EvalMetricsCapability
from vista_backend.api.api import app
from vista_backend.config import settings
from vista_backend.db.ownership import uid
from vista_backend.db.schemas import (
    BackgroundTaskTable,
    ForumThreadTable,
    KnowledgeBaseTable,
    LocalSettingsUpdate,
    Publication,
    SkillTable,
    SlackLinkTable,
    UserTable,
)
from vista_backend.metrics import MetricsRecorder, MetricsSettings
from vista_backend.services import campaign as campaign_service
from vista_backend.services import debate as debate_service
from vista_backend.services import inbox, local_settings
from vista_backend.services.current_user import get_current_user
from vista_backend.utils.misc import now_iso

pytestmark = pytest.mark.anyio

CANARY = "CANARY-ALICE-5e1f0c"
S3M_CANARY = "CANARY-S3M-TOKEN-93b1c7e4ffa2"

OWNED, USER, PUBLIC = "owned", "user", "public"


# ---------------------------------------------------------------------------
# The route table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """
    What Bob sends to a route, and what he must get.

    `json`, `params`, `data` and `files` may hold placeholders (`{chat}`, `{project}`,
    ...: the keys of `World.subs`). `a` and `b` are the statuses allowed in worlds A
    and B (None: any, the response is only searched for the canary).
    """

    kind: str
    json: Any = None
    params: dict[str, str] | None = None
    data: dict[str, Any] | None = None
    files: tuple[tuple[str, tuple[str, bytes]], ...] | None = None
    a: frozenset[int] | None = frozenset({404})
    child: bool = False
    """ Names something only Alice has: Bob is refused in world B too, by `b`. """
    b: frozenset[int] | None = frozenset({404})
    modes: str = "AB"
    """ "A" where world B would start an agent or reach a server; the route is refused before in A. """
    why: str = ""


def owned(**kw: Any) -> Case:
    return Case(OWNED, **kw)


def child(**kw: Any) -> Case:
    return Case(OWNED, child=True, **kw)


def user(**kw: Any) -> Case:
    kw.setdefault("a", None)
    kw.setdefault("modes", "A")
    return Case(USER, **kw)


PUBLIC_CASE = Case(PUBLIC, a=None, modes="")

NOT_FOUND = frozenset({404})
NO_SERVER = frozenset({404, 503})
NO_LAB = frozenset({404, 503})  # Bob's project has no Hypothesis Lab: refused as such
_P = "/projects/{project_name}"
_CHAT = _P + "/chat-sessions/{chat_session_id}"
_UPLOAD = (("files", ("x.txt", b"x")),)

ROUTES: dict[tuple[str, str], Case] = {
    # --- public: no account, no data ---------------------------------------
    ("GET", "/version"): PUBLIC_CASE,
    ("GET", "/openapi.json"): PUBLIC_CASE,
    ("GET", "/docs"): PUBLIC_CASE,
    ("GET", "/docs/oauth2-redirect"): PUBLIC_CASE,
    ("GET", "/redoc"): PUBLIC_CASE,
    # --- agent, reports, MCP ----------------------------------------------
    ("POST", _P + "/agent/run"): child(
        json={"user_prompt": "hi", "chat_session_id": "{chat}"},
        b=frozenset({404, 409}),
        why="a 409 is the missing inference credential, before any turn",
    ),
    ("POST", _P + "/reports/generate"): child(
        json={"chat_session_id": "{chat}"},
        why="Bob names Alice's chat, in a project of the same name",
    ),
    ("POST", _P + "/reports"): owned(
        json={
            "title": "t",
            "summary": "s",
            "slug": "r",
            "body": "b",
            "chat_session_id": "{chat}",
        },
        why="the chat id only tags the file Bob writes in his own project's uploads; nothing of Alice's is read",
    ),
    ("GET", _P + "/mcp/tools"): child(
        params={"chat_session_id": "{chat}"},
        why="needs a running agent; refused before one is looked up",
    ),
    ("POST", _P + "/mcp/call"): child(
        json={"name": "run_bash"},
        why="hosted: calls a tool with the server's credentials, so refused outright",
    ),
    ("POST", _P + "/elicitation"): child(json={"id": "p1", "action": "decline"}),
    ("GET", _P + "/models"): owned(modes="A", why="the endpoint is listed over the network"),
    # --- projects ----------------------------------------------------------
    ("GET", "/projects"): user(why="a list: Bob's own (see the list tests)"),
    ("POST", "/projects"): user(
        json={"name": "bobs-new", "skills": [], "knowledge_bases": [], "tools": ["*"]},
        params={"hosted_forum_wanted": "false"},
        a=frozenset({201}),
    ),
    ("GET", _P): owned(),
    ("PUT", _P): owned(
        json={"name": "alice-lab", "skills": [], "knowledge_bases": [], "tools": ["*"]}
    ),
    ("DELETE", _P): owned(),
    ("POST", _P + "/forum/host"): owned(modes="A", why="moves a forum over the network"),
    ("POST", _P + "/publish"): owned(json={}, modes="A", why="publishes to a server"),
    ("POST", _P + "/publish-to-team"): owned(
        json={"team_id": str(uuid.uuid4())}, modes="A", why="publishes to a server"
    ),
    # --- chats and what runs in them --------------------------------------
    ("GET", _P + "/chat-sessions"): owned(),
    ("POST", _P + "/chat-sessions"): owned(json={"title": "t"}),
    ("GET", _P + "/chat-session"): child(params={"chat_session_id": "{chat}"}),
    ("PUT", _P + "/chat-session"): child(
        json={"title": "x"}, params={"chat_session_id": "{chat}"}
    ),
    ("DELETE", _P + "/chat-session"): child(params={"chat_session_id": "{chat}"}),
    ("GET", _CHAT + "/displayed-files"): child(),
    ("GET", _CHAT + "/run/events"): child(),
    ("POST", _CHAT + "/run/stop"): child(),
    ("GET", _P + "/chat-runs/status"): owned(),
    ("GET", _CHAT + "/inbox"): child(),
    ("PATCH", _CHAT + "/inbox/{item_id}"): child(json={"text": "x"}),
    ("DELETE", _CHAT + "/inbox/{item_id}"): child(),
    ("GET", _CHAT + "/tasks"): child(),
    ("POST", _CHAT + "/tasks/{task_id}/stop"): child(),
    ("GET", _CHAT + "/tasks/{task_id}/log"): child(),
    # --- campaigns ---------------------------------------------------------
    ("POST", _P + "/campaigns"): owned(json={"domain": "d", "planner_skill": "p"}),
    ("GET", _P + "/campaigns"): owned(),
    ("GET", _P + "/campaigns/{run_id}"): child(),
    ("PATCH", _P + "/campaigns/{run_id}"): child(json={"title": "x"}),
    # --- the Hypothesis Lab (debates and threads) ---------------------------
    ("GET", _P + "/forum/status"): owned(),
    ("GET", _P + "/threads"): owned(),
    ("POST", _P + "/threads"): owned(json={"topic": "t"}, b=NO_LAB),
    ("GET", _P + "/threads/{thread_id}"): child(),
    ("POST", _P + "/threads/{thread_id}/posts"): child(json={"body": "x"}, b=NO_LAB),
    ("POST", _P + "/threads/{thread_id}/runs"): child(json={}, b=NO_LAB),
    ("POST", _P + "/threads/{thread_id}/close"): child(b=NO_LAB),
    ("GET", _P + "/threads/{thread_id}/events"): child(),
    # --- files -------------------------------------------------------------
    ("GET", _P + "/{kind}"): owned(),
    ("POST", _P + "/uploads"): owned(files=_UPLOAD),
    ("GET", _P + "/{kind}/{name}"): child(),
    ("DELETE", _P + "/{kind}/{name}"): child(),
    # --- skills ------------------------------------------------------------
    ("GET", "/skills"): user(why="a list: Bob's own and the global ones"),
    ("GET", "/skills/{name}"): owned(),
    ("POST", "/skills"): owned(
        json={"name": "bobs-skill", "description": "d", "body": "b", "project": "{project}"}
    ),
    ("POST", "/skills/generate"): child(json={"chat_session_id": "{chat}"}),
    ("POST", "/skills/import"): owned(
        json={"url": "http://127.0.0.1:9/x", "project": "{project}"},
        modes="A",
        why="B would fetch the URL",
    ),
    ("POST", "/skills/import/scan"): user(
        json={"url": "http://127.0.0.1:9/x"},
        why="reads a URL, writes nothing, names nothing of anyone's; not called (network)",
        modes="",
    ),
    ("POST", "/skills/import/batch"): owned(
        json={"url": "http://127.0.0.1:9/x", "skills": [], "project": "{project}"},
        modes="A",
        why="B would fetch the URL",
    ),
    ("POST", "/skills/import/upload"): owned(
        data={"paths": "SKILL.md", "project": "{project}"},
        files=(("files", ("SKILL.md", b"---\nname: x\ndescription: d\n---\n")),),
    ),
    ("GET", _P + "/skills/from-output"): owned(),
    ("POST", _P + "/skills/from-output"): owned(json={"folder": "nothing"}),
    ("PATCH", "/skills/{name}"): owned(json={"description": "stolen"}),
    ("POST", "/skills/{name}/publish"): owned(json={}, modes="A", why="publishes to a server"),
    ("POST", "/skills/{name}/publish-to-team"): owned(
        json={"team_id": str(uuid.uuid4())}, modes="A", why="publishes to a server"
    ),
    ("DELETE", "/skills/{name}"): owned(),
    # --- knowledge bases ---------------------------------------------------
    ("GET", "/knowledge-bases"): user(why="a list: Bob's own and the docs corpus"),
    ("GET", "/knowledge-bases/{slug}"): owned(),
    ("POST", "/knowledge-bases"): owned(
        json={"slug": "bobs-kb", "name": "n", "project": "{project}"}
    ),
    ("PUT", "/knowledge-bases/{slug}"): owned(json={"description": "stolen"}),
    ("DELETE", "/knowledge-bases/{slug}"): owned(),
    ("POST", "/knowledge-bases/{slug}/publish"): owned(
        json={},
        a=NO_SERVER,
        modes="A",
        why="publishes to a server; with none set that is refused before the lookup (nothing of Alice's is read)",
    ),
    ("POST", "/knowledge-bases/{slug}/publish-to-team"): owned(
        json={"team_id": str(uuid.uuid4())}, modes="A", why="publishes to a server"
    ),
    ("POST", "/knowledge-bases/{slug}/publications"): owned(
        files=_UPLOAD, modes="A", why="B would start the indexer on Bob's own KB"
    ),
    ("GET", "/knowledge-bases/{slug}/publications/{filename}"): child(),
    ("DELETE", "/knowledge-bases/{slug}/publications/{filename}"): child(),
    # --- Slack -------------------------------------------------------------
    ("GET", "/slack/config"): user(why="the server's Slack app and the caller's own lock"),
    ("PUT", "/slack/config"): user(json={"enabled": False}, a=frozenset({403})),
    ("GET", "/slack/links"): user(why="a list: Bob's own links"),
    ("POST", "/slack/links"): user(json={"code": "nope"}, a=frozenset({400})),
    ("PUT", "/slack/links/{link_id}"): child(json={"locked": False}),
    ("DELETE", "/slack/links/{link_id}"): child(),
    # --- settings and the rest of what is the caller's own ------------------
    ("GET", "/settings"): user(a=frozenset({200})),
    ("GET", "/me"): user(a=frozenset({200})),
    ("PUT", "/settings"): user(json={"odo_remote_dir": "/bobs"}, a=frozenset({200})),
    ("GET", "/settings/inference"): user(a=frozenset({200})),
    ("GET", "/settings/hpc-status"): user(modes="", why="asks the clusters; reads only the caller's credentials"),
    ("POST", "/settings/globus/{cluster}/login"): user(
        modes="", why="pending logins are per user: tests/test_runtime_per_user.py"
    ),
    ("POST", "/settings/globus/{cluster}/code"): user(
        modes="", why="completes with the caller's own pending login and saves to their settings"
    ),
    ("POST", "/updates/check"): user(a=frozenset({403}), why="refused when hosted"),
    ("POST", "/updates/download"): user(
        json={"version": "9.9.9"}, a=frozenset({403}), why="refused when hosted"
    ),
    ("GET", "/updates/download"): user(a=frozenset({200}), why="the host's own download state"),
    ("GET", "/activity"): user(a=frozenset({200}), why="counts the caller's own runs"),
    # --- collaboration: a gateway to the server, as the caller ---------------
    ("GET", "/collaboration/status"): user(modes="", why="the caller's own sign-in; tests/test_collaboration_hosted_token.py"),
    ("POST", "/collaboration/teams"): user(modes="", why="acts on the server as the caller's token"),
    ("GET", "/collaboration/teams/{team_id}/members"): user(modes="", why="the server's, as the caller's token"),
    ("POST", "/collaboration/teams/{team_id}/members"): user(modes="", why="the server's, as the caller's token"),
    ("DELETE", "/collaboration/teams/{team_id}/members/me"): user(modes="", why="the server's, as the caller's token"),
    ("GET", "/collaboration/users"): user(modes="", why="the server's, as the caller's token"),
    ("GET", "/collaboration/skills"): user(modes="", why="the server's, as the caller's token"),
    ("POST", "/collaboration/skills/{item_id}/import"): user(modes="", why="imports into the caller's own library"),
    ("GET", "/collaboration/projects"): user(modes="", why="the server's, as the caller's token"),
    ("POST", "/collaboration/projects/{item_id}/import"): user(modes="", why="imports into the caller's own library"),
    ("GET", "/collaboration/knowledge-bases"): user(modes="", why="the server's, as the caller's token"),
    ("POST", "/collaboration/knowledge-bases/{item_id}/import"): user(modes="", why="imports into the caller's own library"),
}  # fmt: skip


def app_routes() -> set[tuple[str, str]]:
    """Every (method, path) the app answers. A route with no methods (a mount) is `("*", path)`."""
    found: set[tuple[str, str]] = set()
    for route in app.routes:
        path = re.sub(r"\{(\w+):[^}]+\}", r"{\1}", getattr(route, "path", ""))
        methods = getattr(route, "methods", None)
        if not methods:
            found.add(("*", path))
        for method in methods or ():
            if method not in ("HEAD", "OPTIONS"):
                found.add((method, path))
    return found


def _table() -> dict[tuple[str, str], Case]:
    return {
        (m, re.sub(r"\{(\w+):[^}]+\}", r"{\1}", p)): c for (m, p), c in ROUTES.items()
    }


def test_every_route_is_classified():
    """A route in no table fails here: say whether it is owned, the caller's own, or public, and test it."""
    routes, table = app_routes(), set(_table())
    assert not routes - table, (
        "Routes with no case in ROUTES (tests/security/test_tenant_isolation.py): "
        f"{sorted(routes - table)}"
    )
    assert not table - routes, (
        f"ROUTES rows for routes that are gone: {sorted(table - routes)}"
    )


def test_the_table_is_not_trivially_empty_of_owned_routes():
    kinds = [c.kind for c in ROUTES.values()]
    assert kinds.count(OWNED) > 40
    assert all(c.kind != PUBLIC or c.modes == "" for c in ROUTES.values())
    # Every route that is not public has its authentication tested; every
    # `child` or `owned` case has a status it must answer.
    assert all(c.a is not None for c in ROUTES.values() if c.kind == OWNED)


def test_only_version_and_the_api_docs_are_open():
    """No token, no data: only these routes do not resolve who is asking, and the table says so."""
    open_routes: set[tuple[str, str]] = set()
    for route in app.routes:
        path = re.sub(r"\{(\w+):[^}]+\}", r"{\1}", getattr(route, "path", ""))
        signed_in = isinstance(route, APIRoute) and any(
            d.call is get_current_user for d in _walk(route.dependant)
        )
        if not signed_in:
            for method in getattr(route, "methods", None) or ("*",):
                if method not in ("HEAD", "OPTIONS"):
                    open_routes.add((method, path))
    assert open_routes == {k for k, c in _table().items() if c.kind == PUBLIC}


async def test_hosted_serves_no_api_docs(session, hosted):
    """The docs and schema map every route for anyone: hosted answers 404; `/version` stays."""
    with api_client(session) as (client, _):
        for path in ("/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"):
            assert (await client.get(path)).status_code == 404, path
        assert (await client.get("/version")).status_code == 200


async def test_a_desktop_still_serves_the_api_docs(session):
    with api_client(session) as (client, _):
        assert (await client.get("/openapi.json")).status_code == 200
        assert (await client.get("/docs")).status_code == 200


def _walk(dependant: Any):
    for d in dependant.dependencies:
        yield d
        yield from _walk(d)


# ---------------------------------------------------------------------------
# Alice's world
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")


@pytest.fixture(autouse=True)
def _no_background_reconcile(monkeypatch):
    """A knowledge base read may queue a write-back of what it reconciled, on the real engine: not here."""
    from vista_backend.api import knowledge_bases as kb_api

    monkeypatch.setattr(kb_api, "_schedule_persist", lambda bg, kb, pubs: now_iso())


class VolumePool:
    """
    Stands in for the agent pool where a route only wants a sandbox's folders:
    the real `ProjectAgent`'s paths (`agent_volume_root`, by the key's user and
    project), with no MCP server started.
    """

    @asynccontextmanager
    async def get(self, key):
        root = agent_volume_root(key[2], key[0])
        yield SimpleNamespace(
            uploads_dir=root / "data" / "uploads", output_dir=root / "data" / "output"
        )


@pytest.fixture(autouse=True)
def _volume_pool(monkeypatch):
    from vista_backend.services import displayed_files, files, reports

    for module in (files, displayed_files, reports):
        monkeypatch.setattr(module, "project_agent_pool", VolumePool())


@pytest.fixture
def alice(hosted):
    return bearer(hosted.token(sub="alice", name="Alice", email="alice@ornl.gov"))


@pytest.fixture
def bob(hosted):
    return bearer(hosted.token(sub="bob", name="Bob", email="bob@ornl.gov"))


@dataclass
class World:
    """Alice's things, by the placeholder that names them in a `Case`."""

    subs: dict[str, str] = field(default_factory=dict)
    alice_id: str = ""
    bob_id: str = ""
    volumes: Path | None = None


def _project(name: str, prompt: str | None = None) -> dict:
    return {
        "name": name,
        "skills": [],
        "knowledge_bases": [],
        "tools": ["*"],
        "system_prompt": prompt,
    }


async def _user_id(session, sub: str) -> str:
    session.expire_all()
    row = (await session.exec(select(UserTable).where(UserTable.sub == sub))).one()
    return uid(row)


async def build_alice(client, session, alice, bob) -> World:
    """Alice makes one of everything an owned route can name, every one holding `CANARY`."""
    w = World()
    res = await client.post(
        "/projects",
        json=_project("alice-lab", CANARY),
        params={"hosted_forum_wanted": "false"},
        headers=alice,
    )
    assert res.status_code == 201, res.text
    project_id = uuid.UUID(res.json()["id"])
    # Bob's first request makes his account.
    assert (await client.get("/projects", headers=bob)).status_code == 200
    w.alice_id = await _user_id(session, "alice")
    w.bob_id = await _user_id(session, "bob")

    chat = await client.post(
        "/projects/alice-lab/chat-sessions", json={"title": CANARY}, headers=alice
    )
    assert chat.status_code == 201, chat.text
    chat_id = uuid.UUID(chat.json()["id"])

    run = await campaign_service.create_campaign(
        session, project_id=project_id, domain="d", planner_skill="p", title=CANARY
    )
    run_id = run.id  # before a commit expires the row
    await debate_service.create_debate(
        session, project_id=project_id, topic=CANARY, thread_id="alice-thread"
    )
    session.add(
        ForumThreadTable(
            project_id=project_id,
            thread_id="alice-thread",
            title=CANARY,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )
    task_id = uuid.uuid4()
    session.add(
        BackgroundTaskTable(
            id=task_id,
            user_id=w.alice_id,
            chat_session_id=chat_id,
            project_id=project_id,
            label=CANARY,
            kind="command",
            state="running",
            command=CANARY,
            created_at=now_iso(),
        )
    )
    link = SlackLinkTable(
        id=(link_id := uuid.uuid4()),
        team_id="T-ALICE",
        slack_user_id="U-ALICE",
        user_id=w.alice_id,
        dm_channel_id="D-ALICE",
        created_at=now_iso(),
    )
    session.add(link)
    await session.commit()
    item = await inbox.put(session, chat_id, kind="message", text=CANARY, notify=False)

    skill = await client.post(
        "/skills",
        json={"name": "alice-skill", "description": CANARY, "body": CANARY},
        headers=alice,
    )
    assert skill.status_code == 201, skill.text
    kb = await client.post(
        "/knowledge-bases",
        json={"slug": "alice-kb", "name": "alice-kb", "description": CANARY},
        headers=alice,
    )
    assert kb.status_code == 201, kb.text
    row = (
        await session.exec(
            select(KnowledgeBaseTable).where(KnowledgeBaseTable.slug == "alice-kb")
        )
    ).one()
    pdfs = Path(row.pdfs_dir)
    pdfs.mkdir(parents=True, exist_ok=True)
    (pdfs / "CANARY-paper.pdf").write_bytes(b"%PDF-1.4 " + CANARY.encode())
    row.publications = [  # type: ignore[assignment]
        Publication(
            filename="CANARY-paper.pdf",
            size=10,
            added_at=now_iso(),
            has_pdf=True,
            index_status="unindexed",
        ).model_dump()
    ]
    session.add(row)
    await session.commit()

    root = agent_volume_root(project_id, w.alice_id)
    for sub in ("uploads", "output"):
        (root / "data" / sub).mkdir(parents=True, exist_ok=True)
        (root / "data" / sub / "CANARY-notes.txt").write_text(CANARY, encoding="utf-8")
    w.volumes = root

    await local_settings.update(
        session,
        LocalSettingsUpdate.model_validate(
            {"odo_remote_dir": f"/alice/{CANARY}", "odo_s3m_token": S3M_CANARY}
        ),
        user_id=w.alice_id,
    )
    await session.commit()

    w.subs = {
        "project": "alice-lab",
        "project_name": "alice-lab",
        "chat": str(chat_id),
        "chat_session_id": str(chat_id),
        "run_id": str(run_id),
        "thread_id": "alice-thread",
        "task_id": str(task_id),
        "item_id": str(item.id),
        "slug": "alice-kb",
        "link_id": str(link_id),
        "filename": "CANARY-paper.pdf",
        "kind": "uploads",
        "file": "CANARY-notes.txt",
        "cluster": "odo",
        "team_id": str(uuid.uuid4()),
    }
    return w


async def build_bob_namesakes(client, bob) -> None:
    """World B: Bob has a project, skill and knowledge base of Alice's names, and his own."""
    res = await client.post(
        "/projects",
        json=_project("alice-lab", "bobs"),
        params={"hosted_forum_wanted": "false"},
        headers=bob,
    )
    assert res.status_code == 201, res.text
    res = await client.post(
        "/skills",
        json={"name": "alice-skill", "description": "bobs", "body": "bobs"},
        headers=bob,
    )
    assert res.status_code == 201, res.text
    res = await client.post(
        "/knowledge-bases",
        json={"slug": "alice-kb", "name": "alice-kb", "description": "bobs"},
        headers=bob,
    )
    assert res.status_code == 201, res.text


def _fill(value: Any, subs: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format_map(subs) if "{" in value else value
    if isinstance(value, dict):
        return {k: _fill(v, subs) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, subs) for v in value]
    return value


def _url(path: str, subs: dict[str, str]) -> str:
    names = dict(subs)
    names["name"] = "alice-skill" if path.startswith("/skills") else subs["file"]
    return path.format_map(names)


async def call(client, key, case: Case, subs: dict[str, str], headers: dict):
    method, path = key
    kwargs: dict[str, Any] = {"headers": headers}
    if case.json is not None:
        kwargs["json"] = _fill(case.json, subs)
    if case.params:
        kwargs["params"] = _fill(case.params, subs)
    if case.data:
        kwargs["data"] = _fill(case.data, subs)
    if case.files:
        kwargs["files"] = list(case.files)
    return await client.request(method, _url(path, subs), **kwargs)


async def assert_alice_intact(client, alice, w: World, why: str) -> None:
    """What Bob did changed nothing of hers."""
    checks = [
        "/projects/alice-lab",
        "/skills/alice-skill",
        "/knowledge-bases/alice-kb",
        f"/projects/alice-lab/chat-session?chat_session_id={w.subs['chat']}",
        f"/projects/alice-lab/campaigns/{w.subs['run_id']}",
        f"/projects/alice-lab/chat-sessions/{w.subs['chat']}/inbox",
    ]
    for url in checks:
        res = await client.get(url, headers=alice)
        assert res.status_code == 200, (why, url, res.text)
    assert CANARY in (await client.get("/projects/alice-lab", headers=alice)).text, why
    assert CANARY in (await client.get("/skills/alice-skill", headers=alice)).text, why
    assert (
        CANARY in (await client.get("/knowledge-bases/alice-kb", headers=alice)).text
    ), why
    links = (await client.get("/slack/links", headers=alice)).json()
    assert [row["id"] for row in links] == [w.subs["link_id"]], why
    for kind in ("uploads", "outputs"):
        got = await client.get(
            f"/projects/alice-lab/{kind}/CANARY-notes.txt", headers=alice
        )
        assert got.status_code == 200 and CANARY in got.text, (why, kind)
    got = await client.get(
        "/knowledge-bases/alice-kb/publications/CANARY-paper.pdf", headers=alice
    )
    assert got.status_code == 200, (why, got.text)


def _cases(*, owned_only: bool, world: str):
    out = []
    for key, case in sorted(_table().items()):
        if owned_only and case.kind != OWNED:
            continue
        if not owned_only and (case.kind != USER or world not in case.modes):
            continue
        if world not in case.modes:
            continue
        out.append(pytest.param(key, case, id=f"{key[0]} {key[1]}"))
    return out


def _leak_free(res, why: str) -> None:
    assert CANARY not in res.text, (why, res.status_code, res.text[:300])
    assert S3M_CANARY not in res.text, (why, res.text[:300])


# ---------------------------------------------------------------------------
# 1. Every owned route: Bob is refused
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key,case", _cases(owned_only=True, world="A"))
async def test_owned_route_is_404_for_bob_when_he_has_none_of_it(
    session, alice, bob, key, case
):
    with api_client(session) as (client, _):
        w = await build_alice(client, session, alice, bob)
        res = await call(client, key, case, w.subs, bob)
        why = f"A {key}"
        assert res.status_code in case.a, (why, res.status_code, res.text[:300])
        _leak_free(res, why)
        await assert_alice_intact(client, alice, w, why)


@pytest.mark.parametrize("key,case", _cases(owned_only=True, world="B"))
async def test_owned_route_never_answers_bob_with_alices_in_a_namesake_world(
    session, alice, bob, key, case
):
    with api_client(session) as (client, _):
        w = await build_alice(client, session, alice, bob)
        await build_bob_namesakes(client, bob)
        res = await call(client, key, case, w.subs, bob)
        why = f"B {key}"
        if case.child:
            assert res.status_code in case.b, (why, res.status_code, res.text[:300])
        _leak_free(res, why)
        await assert_alice_intact(client, alice, w, why)


@pytest.mark.parametrize("key,case", _cases(owned_only=False, world="A"))
async def test_user_level_route_shows_bob_nothing_of_alices(
    session, alice, bob, key, case
):
    with api_client(session) as (client, _):
        w = await build_alice(client, session, alice, bob)
        res = await call(client, key, case, w.subs, bob)
        why = f"user-level {key}"
        if case.a is not None:
            assert res.status_code in case.a, (why, res.status_code, res.text[:300])
        _leak_free(res, why)
        await assert_alice_intact(client, alice, w, why)


@pytest.mark.parametrize(
    "key,case",
    [
        pytest.param(k, c, id=f"{k[0]} {k[1]}")
        for k, c in sorted(_table().items())
        if c.kind != PUBLIC
    ],
)
async def test_no_route_answers_without_a_valid_token(session, hosted, key, case):
    dummy = {
        n: str(uuid.uuid4()) if n.endswith(("id", "_id", "run_id")) else "x"
        for n in re.findall(r"\{(\w+)\}", key[1])
    }
    dummy.update({"kind": "uploads", "item_id": "1"})
    with api_client(session) as (client, _):
        for headers in (
            {},
            bearer("not-a-jwt"),
            bearer(hosted.token(sub="s", roles=[])),
        ):
            res = await client.request(
                key[0], key[1].format_map(dummy), headers=headers
            )
            assert res.status_code in (401, 403), (key, headers, res.status_code)


# ---------------------------------------------------------------------------
# 2. Lists: Bob's own, and the global rows
# ---------------------------------------------------------------------------


async def test_lists_show_bob_only_his_own_and_the_global_rows(session, alice, bob):
    with api_client(session) as (client, _):
        w = await build_alice(client, session, alice, bob)
        session.add(
            SkillTable(
                name="bundled-one",
                description="Ships with VISTA.",
                path="skills/bundled-one",
                bundled=True,
                user_id=None,
                created_at=now_iso(),
                updated_at=now_iso(),
            )
        )
        await session.commit()

        async def names(who, url, key="name"):
            res = await client.get(url, headers=who)
            assert res.status_code == 200, (url, res.text)
            if who is bob:
                _leak_free(res, url)
            return {row[key] for row in res.json()}

        # Positive controls: she sees hers.
        assert "alice-lab" in await names(alice, "/projects")
        assert "alice-skill" in await names(alice, "/skills")
        assert "alice-kb" in await names(alice, "/knowledge-bases", "slug")

        bobs_projects = await names(bob, "/projects")
        assert bobs_projects == {"hello-olcf"}
        bobs_skills = await names(bob, "/skills")
        assert "alice-skill" not in bobs_skills and "bundled-one" in bobs_skills
        bobs_kbs = await names(bob, "/knowledge-bases", "slug")
        assert "alice-kb" not in bobs_kbs

        # Everything of Bob's that lists children of a project Bob does not have is 404, and his own are empty.
        res = await client.post(
            "/projects/hello-olcf/chat-sessions", json={"title": "b"}, headers=bob
        )
        assert res.status_code == 201
        chats = (
            await client.get("/projects/hello-olcf/chat-sessions", headers=bob)
        ).json()
        assert [c["title"] for c in chats] == ["b"]
        assert (
            await client.get("/projects/hello-olcf/campaigns", headers=bob)
        ).json() == []
        assert (
            await client.get("/projects/hello-olcf/threads", headers=bob)
        ).json() == []
        assert (
            await client.get(
                f"/projects/hello-olcf/chat-sessions/{res.json()['id']}/tasks",
                headers=bob,
            )
        ).json() == []
        assert (await client.get("/slack/links", headers=bob)).json() == []
        assert (
            await client.get("/projects/hello-olcf/uploads", headers=bob)
        ).json() == []

        # Alice's lists of the same are hers.
        assert len((await client.get("/slack/links", headers=alice)).json()) == 1
        tasks = (
            await client.get(
                f"/projects/alice-lab/chat-sessions/{w.subs['chat']}/tasks",
                headers=alice,
            )
        ).json()
        assert [t["id"] for t in tasks] == [w.subs["task_id"]]
        uploads = (
            await client.get("/projects/alice-lab/uploads", headers=alice)
        ).json()
        assert [u["name"] for u in uploads] == ["CANARY-notes.txt"]

        # Activity counts his own.
        activity = (await client.get("/activity", headers=bob)).json()
        assert activity["debates"] == 0
        assert all(not v for k, v in activity.items() if isinstance(v, list))


async def test_settings_are_each_users_own(session, alice, bob):
    with api_client(session) as (client, _):
        w = await build_alice(client, session, alice, bob)
        mine = (await client.get("/settings", headers=alice)).json()
        theirs = (await client.get("/settings", headers=bob)).json()
        assert mine["odo_remote_dir"] == f"/alice/{CANARY}"
        assert mine["has_odo_s3m_token"] is True
        assert theirs["odo_remote_dir"] != mine["odo_remote_dir"]
        assert theirs["has_odo_s3m_token"] is False
        saved = await client.put(
            "/settings", json={"odo_remote_dir": "/bobs"}, headers=bob
        )
        assert saved.status_code == 200
        again = (await client.get("/settings", headers=alice)).json()
        assert again["odo_remote_dir"] == f"/alice/{CANARY}", w.alice_id
        # No settings route ever sends a token back.
        for who in (alice, bob):
            assert S3M_CANARY not in (await client.get("/settings", headers=who)).text


# ---------------------------------------------------------------------------
# 3. Sandbox volumes, credentials and the metrics log (the E15 canary)
# ---------------------------------------------------------------------------


def scan_tree(root: Path, needle: str) -> list[Path]:
    """Every file under `root` whose content contains `needle`."""
    return [
        p
        for p in root.rglob("*")
        if p.is_file() and needle in p.read_text(errors="ignore", encoding="utf-8")
    ]


@pytest.fixture
def tenant_env(tmp_path, monkeypatch, session):
    """The real volume and metrics seams, on a temporary data directory and a perf-level metrics log."""
    monkeypatch.setattr(agents_module, "get_engine", lambda: session.bind)
    recorder = MetricsRecorder(
        MetricsSettings(level="perf"), default_log_path=tmp_path / "metrics.jsonl"
    )
    monkeypatch.setattr(metrics_module, "_recorder", recorder)
    return tmp_path


async def _two_users(session):
    session.add_all(
        [UserTable(id="alice", sub="alice"), UserTable(id="bob", sub="bob")]
    )
    await session.commit()


async def test_the_same_project_id_has_a_volume_per_user_and_skills_stay_with_their_owner(
    tenant_env, session
):
    await _two_users(session)
    skill_dir = settings.data_dir / "storage" / "salt-analysis"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: salt-analysis\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    session.add(
        SkillTable(
            name="salt-analysis",
            description="d",
            path="storage/salt-analysis",
            user_id="alice",
            created_at=now_iso(),
            updated_at=now_iso(),
        )
    )
    await session.commit()
    project = make_project(name="shared-id", skills=["salt-analysis"])
    alices = ProjectAgent(
        project, make_local_settings(), uuid.uuid4(), owner_id="alice"
    )
    bobs = ProjectAgent(project, make_local_settings(), uuid.uuid4(), owner_id="bob")

    assert alices.volume_root != bobs.volume_root
    assert alices.volume_root == settings.data_dir / "volumes" / "alice" / str(
        project.id
    )
    assert bobs.volume_root == settings.data_dir / "volumes" / "bob" / str(project.id)
    assert not alices.volume_root.is_relative_to(bobs.volume_root)
    assert not bobs.volume_root.is_relative_to(alices.volume_root)

    await alices._setup_volumes()
    await bobs._setup_volumes()
    # Alice's skill is staged for her; Bob's project names it, and he has no such skill.
    assert (alices.skills_volume_dir / "salt-analysis" / "SKILL.md").exists()
    assert not any(bobs.skills_volume_dir.iterdir())

    alices.output_dir.mkdir(parents=True, exist_ok=True)
    (alices.output_dir / "job.log").write_text(
        f"submitted with token {S3M_CANARY}", encoding="utf-8"
    )
    assert scan_tree(alices.volume_root, S3M_CANARY)  # positive control
    assert scan_tree(bobs.volume_root, S3M_CANARY) == []


async def _drive(agent: ProjectAgent, jobs: int) -> list[dict]:
    """The backend's real tool-call path, as a dry-run campaign takes it: HPC calls beside sandbox ones."""
    captured: list[dict] = []

    async def call_tool(name, args, metadata):
        captured.append({"tool": name, "metadata": metadata})
        await asyncio.sleep(0)  # interleave the two users
        return {"ok": True}

    callback = agent._make_mcp_process_tool_call()
    capability = EvalMetricsCapability(
        session_id=agent.id,
        project_id=str(agent.project.id),
        skills_loaded=lambda: list(agent.project.skills),
        attribute_skill=agent._attribute_skill,
    )

    async def execute(tool_name: str, args: dict[str, object]):
        async def handler(validated):
            return await callback(None, call_tool, tool_name, validated)

        return await capability.wrap_tool_execute(
            None,
            call=None,
            tool_def=type("ToolDef", (), {"name": tool_name})(),
            args=args,
            handler=handler,
        )

    async def run():
        for i in range(jobs):
            await execute("submit_hpc_job", {"job": i})
            await execute("run_bash", {"cmd": f"echo {i}"})

    await capability.wrap_run(None, handler=run)
    return captured


async def test_a_credential_in_alices_settings_reaches_her_hpc_tools_and_nothing_of_bobs(
    tenant_env, session
):
    await _two_users(session)
    alices = ProjectAgent(
        make_project(name="a"),
        make_local_settings(odo_s3m_token=S3M_CANARY),
        uuid.uuid4(),
        owner_id="alice",
    )
    bobs = ProjectAgent(
        make_project(name="b"), make_local_settings(), uuid.uuid4(), owner_id="bob"
    )

    # Positive control: HPC tools carry it for Alice, and only HPC tools.
    assert S3M_CANARY in json.dumps(alices._build_vista_metadata("submit_hpc_job"))
    assert S3M_CANARY not in json.dumps(alices._build_vista_metadata("run_bash"))
    assert S3M_CANARY not in json.dumps(bobs._build_vista_metadata("submit_hpc_job"))
    assert bobs._build_vista_metadata("submit_hpc_job")["vista"]["user_id"] == "bob"

    captured_a, captured_b = await asyncio.gather(_drive(alices, 5), _drive(bobs, 5))
    assert S3M_CANARY in json.dumps(captured_a, default=str)
    for c in captured_a:
        if c["tool"] != "submit_hpc_job":
            assert S3M_CANARY not in json.dumps(c, default=str)
    assert S3M_CANARY not in json.dumps(captured_b, default=str)

    # Not in Bob's volume tree, and not in the shared metrics log, whose events keep their own session.
    assert (
        scan_tree(bobs.volume_root.parent, S3M_CANARY) == []
        if bobs.volume_root.parent.exists()
        else True
    )
    text = (tenant_env / "metrics.jsonl").read_text(encoding="utf-8")
    assert S3M_CANARY not in text
    events = [json.loads(line) for line in text.splitlines()]
    tool_events = [e for e in events if e["event_type"] == "tool_call.client"]
    assert len(tool_events) == 20
    assert {e["session_id"] for e in tool_events} == {alices.id, bobs.id}
