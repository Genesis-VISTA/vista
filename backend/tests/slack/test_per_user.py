"""
Slack on a hosted deployment: each link belongs to the user who entered its
code, and everything done through it acts as, and goes to, that user only
(openspec multi-user-hosted, group 12 and the Slack half of 7.3).
"""

# Fixtures imported from the neighbours "redefine" their names in each test.
# ruff: noqa: F811, F401

import logging
import uuid
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from harness import scripted_model, say
from test_chat_run import env  # noqa: F401  (scripted agents and a registry)
from vista_backend.api.slack import (
    SlackLinkCreate,
    SlackLinkUpdate,
    create_slack_link,
    delete_slack_link,
    get_slack_config,
    list_slack_links,
    update_slack_config,
    update_slack_link,
)
from vista_backend.config import settings
from vista_backend.db.ownership import OwnerMissing
from vista_backend.db.schemas import (
    ChatSessionTable,
    HpcJobRecordTable,
    SlackConfigTable,
    SlackLinkTable,
    UserTable,
)
from vista_backend.services import chat_run, collaboration, inbox
from vista_backend.services.current_user import (
    NoUserContext,
    current_user_id,
    use_user,
)
from vista_backend.slack import credentials, limits, linking, service, store
from vista_backend.slack.credentials import CredentialReminder, Expiry
from vista_backend.slack.limits import LOCKED, check, refusal
from vista_backend.slack.threads import (
    NoProject,
    NotYourThread,
    conversation_for,
    project_for,
)

from .conftest import TEAM, link, project
from .fakes import FakeTransport
from .test_service import (  # noqa: F401  (fixtures and helpers)
    _answers,
    _dm,
    _settled,
    _slack_conversation,
    credential,
    db,
    on,
    slack,
)

pytestmark = pytest.mark.anyio

ALICE = UserTable(id="alice", sub="sub-alice")
BOB = UserTable(id="bob", sub="sub-bob")
SUBMIT = "vista__submit_hpc_job"


@pytest.fixture(autouse=True)
def fresh_known(monkeypatch):
    monkeypatch.setattr(credentials, "_known", {})


@pytest.fixture
async def users(session):
    """Alice and Bob exist as users."""
    for user in (ALICE, BOB):
        session.add(UserTable(id=user.id, sub=user.sub))
    await session.commit()


@pytest.fixture
def hosted_slack_on(monkeypatch, users):
    """The switch to hosted, thrown by a test once its fixtures (built for a desktop) are set up."""

    def flip() -> None:
        _go_hosted(monkeypatch)

    return flip


@pytest.fixture
def hosted_slack(monkeypatch, users):
    """
    A hosted deployment whose Slack secrets still come from the test's keyring
    (a hosted one reads its secrets directory, which is no different to code).
    """
    _go_hosted(monkeypatch)


def _go_hosted(monkeypatch) -> None:
    monkeypatch.setattr(settings.secrets, "backend", "os")
    monkeypatch.setattr(settings, "deployment", "hosted")

    async def answering(provider):
        """The server's own model is up (a hosted default; nothing answers in a test)."""

    from vista_backend.services import inference_probe

    monkeypatch.setattr(inference_probe, "require_server_model", answering)
    monkeypatch.setattr(chat_run, "require_server_model", answering)


@pytest.fixture(autouse=True)
def signed_out(monkeypatch):
    async def status(local):
        return collaboration.CollaborationStatus(
            configured=False,
            reachable=False,
            reason="not configured",
            url=None,
            source=None,
        )

    monkeypatch.setattr(collaboration, "status", status)


async def _code(session, slack_user: str) -> str:
    return await linking.issue_code(
        session, team_id=TEAM, slack_user_id=slack_user, dm_channel_id=f"D{slack_user}"
    )


# -- linking -----------------------------------------------------------------


async def test_each_user_links_their_own_slack_account(session, local, hosted_slack):
    a = await create_slack_link(
        SlackLinkCreate(code=await _code(session, "U1")), session, local, ALICE
    )
    b = await create_slack_link(
        SlackLinkCreate(code=await _code(session, "U2")), session, local, BOB
    )

    rows = {
        r.slack_user_id: r.user_id
        for r in (await session.exec(select(SlackLinkTable))).all()
    }
    assert rows == {"U1": "alice", "U2": "bob"}
    assert [v.id for v in await list_slack_links(session, ALICE)] == [a.id]
    assert [v.id for v in await list_slack_links(session, BOB)] == [b.id]


async def test_another_users_link_is_not_found(session, local, hosted_slack):
    a = await create_slack_link(
        SlackLinkCreate(code=await _code(session, "U1")), session, local, ALICE
    )

    for call in (
        update_slack_link(a.id, SlackLinkUpdate(project_id=None), session, BOB),
        delete_slack_link(a.id, session, BOB),
    ):
        with pytest.raises(HTTPException) as err:
            await call
        assert err.value.status_code == 404
    assert len(await list_slack_links(session, ALICE)) == 1


async def test_entering_a_code_as_someone_else_moves_the_link(
    session, local, hosted_slack
):
    first = await create_slack_link(
        SlackLinkCreate(code=await _code(session, "U1")), session, local, ALICE
    )
    mine = await project(session, user_id="alice")
    await update_slack_link(
        first.id, SlackLinkUpdate(project_id=mine.id), session, ALICE
    )

    again = await create_slack_link(
        SlackLinkCreate(code=await _code(session, "U1")), session, local, BOB
    )

    assert again.id == first.id and again.project_id is None
    assert await list_slack_links(session, ALICE) == []
    assert [v.id for v in await list_slack_links(session, BOB)] == [first.id]


async def test_a_hosted_link_with_no_owner_is_refused(session, hosted_slack):
    session.add(
        SlackLinkTable(team_id=TEAM, slack_user_id="U9", created_at=store.now_iso())
    )

    with pytest.raises(OwnerMissing):
        await session.commit()


async def test_the_desktop_link_is_local(session):
    row = await link(session, "U1")
    await session.refresh(row)
    assert row.user_id == "local"


# -- whose projects and conversations ----------------------------------------


def _msg(ts="10.0", user="U1", channel="D1"):
    from vista_backend.slack.events import Message

    return Message(
        team_id=TEAM, channel_id=channel, user_id=user, text="hi", ts=ts, is_dm=True
    )


async def test_a_conversation_is_the_links_users_in_their_project(
    session, hosted_slack
):
    a_id = (await project(session, "alice-proj", user_id="alice")).id
    b_id = (await project(session, "bob-proj", user_id="bob")).id

    first = await conversation_for(
        session, _msg(user="U1"), project_id=None, user_id="alice"
    )
    first_project, first_chat = first.project.id, first.chat_session_id
    second = await conversation_for(
        session, _msg(user="U2", channel="D2"), project_id=None, user_id="bob"
    )

    assert (first_project, second.project.id) == (a_id, b_id)
    owners = {
        c.id: c.user_id for c in (await session.exec(select(ChatSessionTable))).all()
    }
    assert owners[first_chat] == "alice"
    assert owners[second.chat_session_id] == "bob"


async def test_a_user_with_no_project_does_not_get_anyone_elses(session, hosted_slack):
    await project(session, "alice-proj", user_id="alice")

    with pytest.raises(NoProject):
        await project_for(session, None, "bob")
    with pytest.raises(NoProject):
        await conversation_for(session, _msg(user="U2"), project_id=None, user_id="bob")


async def test_a_thread_started_for_one_user_is_not_continued_for_another(
    session, hosted_slack
):
    await project(session, "alice-proj", user_id="alice")
    await project(session, "bob-proj", user_id="bob")
    await conversation_for(session, _msg(user="U1"), project_id=None, user_id="alice")

    # The same Slack account, linked to someone else since.
    with pytest.raises(NotYourThread):
        await conversation_for(session, _msg(user="U1"), project_id=None, user_id="bob")
    # And another Slack account in that thread is refused as ever.
    with pytest.raises(NotYourThread):
        await conversation_for(session, _msg(user="U2"), project_id=None, user_id="bob")


# -- limits, locks, settings -------------------------------------------------


async def _submitted(session, user_id: str, slack_user: str, n: int):
    await link(session, slack_user, user_id=user_id)
    for _ in range(n):
        session.add(
            HpcJobRecordTable(
                origin="slack",
                slack_team_id=TEAM,
                slack_user_id=slack_user,
                cluster="odo",
                job_id=uuid.uuid4().hex,
                job="flibe-md",
                submitted_at=(store.now() - timedelta(hours=1)).isoformat(
                    timespec="seconds"
                ),
            )
        )
    await session.commit()


def _config(**kw) -> SlackConfigTable:
    return SlackConfigTable(
        id=1, allowed_team_ids=[TEAM], allowed_channel_ids=[], max_jobs_per_day=2, **kw
    )


async def test_the_daily_limit_counts_each_users_own_jobs(db, session, hosted_slack):
    await _submitted(session, "alice", "U1", 2)
    await _submitted(session, "bob", "U2", 0)

    with use_user("alice"):
        why = await check(_config(), SUBMIT, {})
    with use_user("bob"):
        bobs = await check(_config(), SUBMIT, {})

    assert why is not None and "limit is 2" in why
    assert bobs is None


async def test_limits_with_no_user_are_the_bug_signal_when_hosted(
    db, session, hosted_slack
):
    with pytest.raises(NoUserContext):
        await check(_config(), SUBMIT, {})


async def test_a_lock_from_slack_is_that_users_alone_when_hosted(
    db, session, hosted_slack
):
    await link(session, "U1", user_id="alice")
    await link(session, "U2", user_id="bob")

    await limits.lock("U1", user_id="alice")

    with use_user("alice"):
        assert await refusal(SUBMIT, {}) == LOCKED
    with use_user("bob"):
        assert await refusal(SUBMIT, {}) is None
    row = await session.get(SlackConfigTable, 1)
    assert row is None or not row.locked, "the server's own lock is untouched"


async def test_a_user_unlocks_their_own_link_only(session, local, hosted_slack):
    a = await create_slack_link(
        SlackLinkCreate(code=await _code(session, "U1")), session, local, ALICE
    )
    await limits.lock("U1", user_id="alice")
    assert [v.locked for v in await list_slack_links(session, ALICE)] == [True]

    with pytest.raises(HTTPException):
        await update_slack_link(a.id, SlackLinkUpdate(locked=False), session, BOB)
    await update_slack_link(a.id, SlackLinkUpdate(locked=False), session, ALICE)

    assert [v.locked for v in await list_slack_links(session, ALICE)] == [False]


async def test_the_app_is_the_servers_when_hosted(session, monkeypatch, hosted_slack):
    monkeypatch.setattr(settings.slack, "hosted_enabled", True)
    monkeypatch.setattr(settings.slack, "hosted_allowed_team_ids", [TEAM])
    monkeypatch.setattr(settings.slack, "hosted_max_nodes", 2)

    view = await get_slack_config(session, ALICE)

    assert view.server_managed and view.enabled
    assert view.max_nodes == 2
    assert view.allowed_team_ids == [] and not view.has_bot_token
    with pytest.raises(HTTPException) as err:
        await update_slack_config(
            __import__("vista_backend.api.slack", fromlist=["x"]).SlackConfigUpdate(
                enabled=False
            ),
            session,
            ALICE,
        )
    assert err.value.status_code == 403
    assert (await store.get_config(session)).allowed_team_ids == [TEAM]


# -- reminders ---------------------------------------------------------------


async def test_a_reminder_goes_to_the_links_user_about_their_own_credentials(
    db, session, hosted_slack
):
    await link(session, "U1", user_id="alice")
    await link(session, "U2", user_id="bob")
    soon = store.now() + timedelta(hours=23)
    wanted = {
        "alice": [Expiry("odo", "S3M token", soon)],
        "bob": [
            Expiry("odo", "S3M token", soon),
            Expiry("frontier", "Globus session", soon),
        ],
    }

    async def expiries(local):
        assert current_user_id() == local.user_id, "worked out as the link's user"
        return wanted[local.user_id]

    slack = FakeTransport()
    assert await CredentialReminder(slack, expiries=expiries).run_once() == 2

    by_dm = {m.channel: m.text for m in slack.messages}
    assert set(by_dm) == {"DU1", "DU2"}
    assert "Odo S3M token" in by_dm["DU1"] and "Frontier" not in by_dm["DU1"]
    assert "Frontier Globus session" in by_dm["DU2"]
    assert [e.cluster for e in credentials.known("alice")] == ["odo"]
    assert len(credentials.known("bob")) == 2


# -- turns -------------------------------------------------------------------


async def _owned_by(env, row, user_id: str) -> None:
    async with AsyncSession(env.engine) as s:
        chat = await s.get(ChatSessionTable, row.id)
        chat.user_id = user_id
        s.add(chat)
        await s.commit()


async def test_each_users_turn_runs_as_that_user(
    env, on, slack, session, credential, users, hosted_slack_on, monkeypatch
):
    await link(session, "U1", user_id="alice")
    await link(session, "U2", user_id="bob")
    rows = {}
    seen: dict[str, str] = {}
    for user, slack_user in (("alice", "U1"), ("bob", "U2")):
        row = await _slack_conversation(env, [say(f"hi {user}")], user=slack_user)
        await _owned_by(env, row, user)
        rows[user] = row

        def driver(messages, info, user=user):
            seen[user] = current_user_id()
            return say(f"hello {user}")

        env.stack.enter_context(
            env.agents[row.id].agent.override(model=scripted_model(driver))
        )
    hosted_slack_on()
    await service.start(host="lab-mac")

    await slack.deliver(_dm("<@UBOT> status?", user="U1"))
    await slack.deliver(_dm("<@UBOT> status?", user="U2"))
    for row in rows.values():
        await _settled(env, row.id)

    assert seen == {"alice": "alice", "bob": "bob"}
    assert any("hello alice" in a for a in _answers(slack, "DU1"))
    assert any("hello bob" in a for a in _answers(slack, "DU2"))
    assert not any("hello bob" in a for a in _answers(slack, "DU1"))


async def test_a_run_held_at_startup_is_followed_as_its_owner(
    env, on, slack, session, credential, users, hosted_slack_on, caplog
):
    await link(session, "U1", user_id="alice")
    row = await _slack_conversation(env, [say("done")], user="U1")
    await _owned_by(env, row, "alice")
    hosted_slack_on()
    service.watch_runs()
    async with AsyncSession(env.engine) as s:
        await inbox.put(s, row.id, kind="message", text="go", notify=False)
    await env.registry.wake(row.id)
    run = env.registry.get(row.id)
    if run is not None:
        await run.wait()

    with caplog.at_level(logging.ERROR, logger="vista.slack"):
        await service.start(host="lab-mac")  # Outside any user context.
        for task in list(service.following.values()):
            await task

    assert any("done" in a for a in _answers(slack, "DU1"))
    assert "Could not show run" not in caplog.text
