"""
Authorization and background owners (openspec change `multi-user-hosted`, task
group 7): another user's run, prompt, task or file answers 404 exactly as a
missing one does, and background work runs as the user whose row it acts on.
"""

import asyncio
import uuid

import pytest
from sqlmodel import select

from harness import api_client
from harness.tokens import bearer
from vista_backend.api import debate as debate_api
from vista_backend.config import settings
from vista_backend.db.schemas import UserTable
from vista_backend.services import chat_run, inbox
from vista_backend.services.current_user import (
    NoUserContext,
    as_user,
    current_user_id,
)

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


async def _project(client, who, name):
    res = await client.post(
        "/projects",
        json={"name": name, "skills": [], "knowledge_bases": [], "tools": ["*"]},
        params={"hosted_forum_wanted": "false"},
        headers=who,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _chat(client, who, project):
    res = await client.post(
        f"/projects/{project}/chat-sessions", json={"title": "t"}, headers=who
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _user_id(session, sub):
    session.expire_all()
    return (await session.exec(select(UserTable).where(UserTable.sub == sub))).one().id


async def test_run_task_inbox_and_file_routes_404_for_another_user(session, alice, bob):
    with api_client(session) as (client, _):
        await _project(client, alice, "same")
        await _project(client, bob, "same")
        await _project(client, alice, "only-alice")
        chat = await _chat(client, alice, "same")
        task = uuid.uuid4()
        base = f"/projects/same/chat-sessions/{chat}"
        calls = [
            ("get", f"{base}/run/events"),
            ("post", f"{base}/run/stop"),
            ("get", f"{base}/inbox"),
            ("get", f"{base}/tasks"),
            ("post", f"{base}/tasks/{task}/stop"),
            ("get", f"{base}/tasks/{task}/log"),
            ("get", f"{base}/displayed-files"),
        ]
        for method, url in calls:
            theirs = await getattr(client, method)(url, headers=bob)
            missing = await getattr(client, method)(
                url.replace(chat, str(uuid.uuid4())), headers=bob
            )
            assert theirs.status_code == 404, (url, theirs.text)
            assert (theirs.status_code, theirs.text) == (
                missing.status_code,
                missing.text,
            ), url
        # Files: Bob has no project of Alice's name.
        for method, url in [
            ("get", "/projects/only-alice/uploads"),
            ("get", "/projects/only-alice/outputs"),
            ("get", "/projects/only-alice/outputs/a.txt"),
            ("get", "/projects/only-alice/uploads/a.txt"),
            ("delete", "/projects/only-alice/outputs/a.txt"),
        ]:
            theirs = await getattr(client, method)(url, headers=bob)
            missing = await getattr(client, method)(
                url.replace("only-alice", "nope"), headers=bob
            )
            assert theirs.status_code == 404, url
            assert theirs.text.replace("only-alice", "nope") == missing.text, url


async def test_elicitation_of_another_users_run_is_404(session, alice, bob):
    with api_client(session) as (client, _):
        a = await _project(client, alice, "alice-p")
        await _project(client, bob, "bob-p")
        run = chat_run.ChatRun(
            chat_session_id=uuid.uuid4(),
            project_id=uuid.UUID(a["id"]),
            agent_key=(None, uuid.UUID(a["id"])),
            user_prompt="x",
            prior_history=[],
        )
        run.pending.add("prompt-1")
        chat_run.chat_run_registry._runs[run.chat_session_id] = run
        try:
            res = await client.post(
                "/projects/bob-p/elicitation",
                json={"id": "prompt-1", "action": "decline"},
                headers=bob,
            )
            missing = await client.post(
                "/projects/bob-p/elicitation",
                json={"id": "nope", "action": "decline"},
                headers=bob,
            )
        finally:
            chat_run.chat_run_registry._runs.pop(run.chat_session_id, None)
    assert res.status_code == 404
    assert res.text == missing.text


async def test_activity_counts_only_the_callers_debates(session, alice, bob):
    gate = asyncio.Event()

    async def hold():
        await gate.wait()

    with api_client(session) as (client, _):
        assert (await client.get("/activity", headers=alice)).status_code == 200
        with as_user(await _user_id(session, "alice")):
            task = debate_api._spawn(hold())
        try:
            mine = (await client.get("/activity", headers=alice)).json()
            theirs = (await client.get("/activity", headers=bob)).json()
        finally:
            gate.set()
            await task
    assert mine["debates"] == 1
    assert theirs["debates"] == 0


async def test_wake_runs_as_the_conversations_owner(session, alice, bob, monkeypatch):
    """A wake from non-request code starts the turn as the chat's owner, per user."""
    seen: dict[str, str] = {}
    with api_client(session) as (client, _):
        await _project(client, alice, "pa")
        await _project(client, bob, "pb")
        ca = await _chat(client, alice, "pa")
        cb = await _chat(client, bob, "pb")
        await session.commit()
        async with chat_run.session_factory() as db:
            for c in (ca, cb):
                await inbox.put(
                    db, uuid.UUID(c), kind="message", text="hi", notify=False
                )

        registry = chat_run.ChatRunRegistry()

        async def fake_start(**kw):
            seen[str(kw["chat_session_id"])] = current_user_id()
            return None

        monkeypatch.setattr(registry, "start", fake_start)
        for c in (ca, cb):
            await registry.wake(uuid.UUID(c))
    assert seen[ca] == await _user_id(session, "alice")
    assert seen[cb] == await _user_id(session, "bob")


async def test_no_user_outside_as_user_when_hosted(hosted):
    with pytest.raises(NoUserContext):
        current_user_id()
    with as_user("u1"):
        assert current_user_id() == "u1"


async def test_job_watch_and_campaign_monitor_run_as_the_rows_owner(
    hosted, monkeypatch
):
    """Each background entry point acts as the user on the row it was given."""
    from vista_backend.db.schemas import BackgroundTaskTable, HpcJobTable
    from vista_backend.services import background_tasks, campaign_monitor
    from vista_backend.services import campaign as campaign_service

    seen: list[tuple[str, str]] = []
    registry = background_tasks.BackgroundTaskRegistry()

    async def fake_watch(watch, read, clock):
        seen.append(("watch", current_user_id()))

    monkeypatch.setattr(registry, "_watch_job", fake_watch)
    for owner in ("u-alice", "u-bob"):
        record = BackgroundTaskTable(
            id=uuid.uuid4(),
            user_id=owner,
            chat_session_id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            label="w",
            kind="job_watch",
            state="watching",
            created_at="2026-01-01T00:00:00+00:00",
        )
        registry.watch_job(record, read=None)  # type: ignore[arg-type]
        await registry._watches[record.id].task
    jobs = [
        HpcJobTable(job_id="1", user_id="u-alice"),
        HpcJobTable(job_id="2", user_id="u-bob"),
    ]

    async def open_jobs(session):
        return jobs

    async def process(self, session, job):
        seen.append(("monitor", current_user_id()))

    monkeypatch.setattr(campaign_service, "list_open_jobs", open_jobs)
    monkeypatch.setattr(campaign_monitor.CampaignMonitor, "_process", process)
    monitor = campaign_monitor.CampaignMonitor(poll=None, collect=None)  # type: ignore[arg-type]
    await monitor.reconcile_once(None)  # type: ignore[arg-type]
    assert seen == [
        ("watch", "u-alice"),
        ("watch", "u-bob"),
        ("monitor", "u-alice"),
        ("monitor", "u-bob"),
    ]
