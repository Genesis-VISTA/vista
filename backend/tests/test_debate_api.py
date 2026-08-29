"""
Tests for the debate API.

Real HTTP through an ASGI transport, a real DB session, and the fake h5i binary,
so the routes, the access boundary and the event stream are all exercised. The
background argument is not started here — these test the surface a client sees,
and the loop has its own driver tests.
"""

import asyncio
import json
import uuid
from pathlib import Path

import anyio
import httpx
import pytest

from vista_backend.config import ForumSettings, settings
from vista_backend.db.schemas import ProjectCreate
from vista_backend.services import debate as debate_service
from vista_backend.services import project as project_service
from vista_backend.services.h5i_forum import ForumClient, ParticipantRole


FAKE = Path(__file__).parent / "fixtures" / "fake_h5i.py"


@pytest.fixture
def forum_config(tmp_path, monkeypatch) -> ForumSettings:
    """Point the whole app at a throwaway forum backed by the fake binary."""
    (tmp_path / ".git" / ".h5i").mkdir(parents=True)
    config = ForumSettings(
        enabled=True, binary=str(FAKE), repo_root=tmp_path, timeout=30.0
    )
    monkeypatch.setattr(settings, "forum", config)
    return config


@pytest.fixture
async def app_client(session, alice, monkeypatch):
    """
    An HTTP client over the real app, sharing the test's DB session and identity.

    Both overrides matter. The session override is what makes assertions
    possible — routes and test see the same uncommitted transaction. The identity
    override stands in for SSO, which resolves a seeded email that does not exist
    in an in-memory database.
    """
    from vista_backend.api.api import app
    from vista_backend.db.db import _get_session
    from vista_backend.services.auth import get_user

    async def _session():
        yield session

    async def _user():
        return alice

    app.dependency_overrides[_get_session] = _session
    app.dependency_overrides[get_user] = _user

    # The event stream deliberately opens its own session, because the generator
    # outlives the request. Point that factory at the test's in-memory database,
    # which the production engine knows nothing about.
    from contextlib import asynccontextmanager

    from vista_backend.api import debate as debate_api

    @asynccontextmanager
    async def _stream_session():
        yield session

    monkeypatch.setattr(debate_api, "stream_session_factory", _stream_session)
    monkeypatch.setattr(debate_api, "STREAM_POLL_SECONDS", 0.01)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def _project(session, user, name="api-debate"):
    return await project_service.create_project(session, ProjectCreate(name=name), user)


async def _run(session, user, project, *, topic="why does the knee move?"):
    """A debate row with a real forum thread behind it."""
    client = ForumClient(settings.forum, confirm_delay=0.0)
    thread_id = await client.create_thread(topic, body="Debate it.")
    run = await debate_service.create_debate(
        session,
        project_id=project.id,
        user_id=user.id,
        topic=topic,
        thread_id=thread_id,
        framing="Debate it.",
        rounds=2,
    )
    participant = await client.create_participant(
        box_slug=f"proposer-{str(run.id)[:8]}",
        identity=f"vista-proposer-{str(run.id)[:8]}",
        role=ParticipantRole.WORKER,
    )
    await debate_service.add_participant(
        session, run_id=run.id, participant=participant, debate_role="proposer"
    )
    await debate_service.set_status(session, run_id=run.id, status="debating")
    return run, client, participant


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_get_returns_run_participants_and_posts(
    forum_config, app_client, session, alice
):
    project = await _project(session, alice)
    run, client, _ = await _run(session, alice, project)
    await debate_service.project_thread(
        session, run_id=run.id, thread=await client.read_thread(run.thread_id)
    )

    resp = await app_client.get(f"/projects/{project.name}/debates/{run.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run"]["topic"] == "why does the knee move?"
    assert body["run"]["status"] == "debating"
    assert [p["debate_role"] for p in body["participants"]] == ["proposer"]
    assert [p["kind"] for p in body["posts"]] == ["TASK"]


@pytest.mark.anyio
async def test_the_post_payload_keeps_provenance_visible(
    forum_config, app_client, session, alice
):
    """
    The UI has to be able to draw the host/claim boundary, so the API must not
    flatten it into one blob of text.
    """
    project = await _project(session, alice)
    run, client, participant = await _run(session, alice, project)
    await client.post_as(
        participant, run.thread_id, "rigidity sets the knee", kind="PROPOSAL"
    )
    await debate_service.project_thread(
        session, run_id=run.id, thread=await client.read_thread(run.thread_id)
    )

    resp = await app_client.get(f"/projects/{project.name}/debates/{run.id}")
    proposal = next(p for p in resp.json()["posts"] if p["kind"] == "PROPOSAL")

    assert proposal["body"] == "rigidity sets the knee"
    for host_stamped in ("sender", "forum_role", "box_id", "policy_digest"):
        assert proposal[host_stamped], f"{host_stamped} must reach the client"
    assert proposal["vouch_lane"] == "host-observed"


@pytest.mark.anyio
async def test_list_is_scoped_to_the_project(forum_config, app_client, session, alice):
    mine = await _project(session, alice, name="mine")
    theirs = await _project(session, alice, name="theirs")
    run, _, _ = await _run(session, alice, mine)

    resp = await app_client.get(f"/projects/{mine.name}/debates")
    assert [r["id"] for r in resp.json()] == [str(run.id)]
    resp = await app_client.get(f"/projects/{theirs.name}/debates")
    assert resp.json() == []


# --------------------------------------------------------------------------- #
# The access boundary
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_debate_is_not_readable_through_another_project(
    forum_config, app_client, session, alice
):
    """Project membership is the boundary; the run id alone must not be enough."""
    mine = await _project(session, alice, name="mine")
    other = await _project(session, alice, name="other")
    run, _, _ = await _run(session, alice, mine)

    resp = await app_client.get(f"/projects/{other.name}/debates/{run.id}")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_an_unknown_debate_is_404(forum_config, app_client, session, alice):
    project = await _project(session, alice)
    resp = await app_client.get(f"/projects/{project.name}/debates/{uuid.uuid4()}")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# The human joining in
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_human_can_post_into_a_live_debate(
    forum_config, app_client, session, alice
):
    project = await _project(session, alice)
    run, client, _ = await _run(session, alice, project)

    resp = await app_client.post(
        f"/projects/{project.name}/debates/{run.id}/posts",
        json={"body": "constrain this to 1 bar", "kind": "ASK"},
    )
    assert resp.status_code == 200
    post = resp.json()
    assert post["sender"] == "human", "the human posts as themselves, not as an agent"
    assert post["kind"] == "ASK"
    assert post["round_index"] is None, "an interjection belongs to no agent round"

    thread = await client.read_thread(run.thread_id)
    assert "constrain this to 1 bar" in [p.body for p in thread.posts]


@pytest.mark.anyio
async def test_the_human_can_still_annotate_a_closed_debate(
    forum_config, app_client, session, alice
):
    """
    Verified against real h5i: closing removes the thread from every *box's*
    inbox and leaves the host's write path open, so the human's post lands on a
    closed thread. Closing ends the argument, not the operator's access to the
    record — the agents are the ones who are stopped.
    """
    project = await _project(session, alice)
    run, client, _ = await _run(session, alice, project)
    await client.close_thread(run.thread_id)

    resp = await app_client.post(
        f"/projects/{project.name}/debates/{run.id}/posts",
        json={"body": "for the record: we stopped because of the shear data"},
    )
    assert resp.status_code == 200
    assert resp.json()["sender"] == "human"


@pytest.mark.anyio
async def test_an_unpostable_kind_is_rejected(forum_config, app_client, session, alice):
    """
    `CLAIM` is a real forum kind that `post --kind` accepts and then discards, so
    the API must refuse it rather than return a post that will never exist.
    """
    project = await _project(session, alice)
    run, _, _ = await _run(session, alice, project)

    resp = await app_client.post(
        f"/projects/{project.name}/debates/{run.id}/posts",
        json={"body": "mine now", "kind": "CLAIM"},
    )
    assert resp.status_code in (400, 422, 500)
    assert resp.status_code != 200


# --------------------------------------------------------------------------- #
# Closing
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_closing_ends_the_debate_on_the_forum_too(
    forum_config, app_client, session, alice
):
    project = await _project(session, alice)
    run, client, _ = await _run(session, alice, project)

    resp = await app_client.post(f"/projects/{project.name}/debates/{run.id}/close")
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"
    assert (await client.read_thread(run.thread_id)).is_closed


# --------------------------------------------------------------------------- #
# The stream
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_stream_replays_the_thread_then_ends_on_a_terminal_status(
    forum_config, app_client, session, alice
):
    """
    A finished debate's stream is finite: every post once, then the status, then
    the connection closes. A stream that hung after the verdict would leave every
    client waiting on a debate that ended.
    """
    project = await _project(session, alice)
    run, client, participant = await _run(session, alice, project)
    await client.post_as(participant, run.thread_id, "claim", kind="PROPOSAL")
    await debate_service.project_thread(
        session, run_id=run.id, thread=await client.read_thread(run.thread_id)
    )
    await debate_service.record_verdict(
        session, run_id=run.id, verdict={"ranked": [], "rationale": "done"}
    )

    events = await _collect_stream(app_client, project.name, run.id)
    kinds = [json.loads(d)["kind"] for e, d in events if e == "post"]
    assert kinds == ["TASK", "PROPOSAL"]

    assert events[-1][0] == "status"
    assert json.loads(events[-1][1])["status"] == "converged"


@pytest.mark.anyio
async def test_the_stream_is_scoped_to_the_project(
    forum_config, app_client, session, alice
):
    mine = await _project(session, alice, name="mine")
    other = await _project(session, alice, name="other")
    run, _, _ = await _run(session, alice, mine)

    resp = await app_client.get(f"/projects/{other.name}/debates/{run.id}/events")
    assert resp.status_code == 404


async def _collect_stream(client, project_name, run_id, limit=50, timeout=10.0):
    """
    Read an SSE response into (event, data) pairs.

    Bounded by a timeout on purpose. The stream ends itself on a terminal status,
    so a run that never terminates means a bug — and without this, that bug
    presents as a suite that hangs rather than a test that fails. (Removing the
    stream's between-poll rollback does exactly that: it re-reads its first
    snapshot forever and never sees the run finish.)
    """

    async def read() -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        async with client.stream(
            "GET", f"/projects/{project_name}/debates/{run_id}/events"
        ) as resp:
            assert resp.status_code == 200
            event = None
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    events.append((event or "message", line.split(":", 1)[1].strip()))
                    if len(events) >= limit:
                        break
        return events

    # anyio's cancel scope rather than asyncio.wait_for: the latter cancels from
    # outside the test runner's task tree and leaves the streaming response
    # half-torn-down, which reports as an error about cancellation instead of the
    # timeout that actually happened.
    with anyio.move_on_after(timeout) as scope:
        return await read()
    if scope.cancelled_caught:
        pytest.fail(
            f"the event stream did not terminate within {timeout}s — "
            "it is not seeing the run reach a terminal status"
        )
    return []


@pytest.mark.anyio
async def test_the_stream_emits_posts_that_arrive_while_it_is_open(
    forum_config, app_client, session, engine, alice, monkeypatch
):
    """
    The polling path, which the replay test above cannot reach: a debate that is
    still running must push new posts to a client already connected.

    Needs committed data and a real second session, because the stream ends its
    transaction between polls — without that it would keep re-reading its first
    snapshot and never see the background task's writes at all.
    """
    from sqlmodel.ext.asyncio.session import AsyncSession

    from vista_backend.api import debate as debate_api

    project = await _project(session, alice)
    run, client, participant = await _run(session, alice, project)
    await debate_service.project_thread(
        session, run_id=run.id, thread=await client.read_thread(run.thread_id)
    )
    # Read these out before committing: `commit()` expires the ORM object, and a
    # later attribute access from another task would try to refresh it there.
    run_id, thread_id, project_name = run.id, run.thread_id, project.name
    await session.commit()

    monkeypatch.setattr(
        debate_api, "stream_session_factory", lambda: AsyncSession(engine)
    )
    async def add_a_post_then_finish():
        await asyncio.sleep(0.05)
        await client.post_as(
            participant, thread_id, "rigidity sets it", kind="PROPOSAL"
        )
        async with AsyncSession(engine) as s:
            await debate_service.project_thread(
                s, run_id=run_id, thread=await client.read_thread(thread_id)
            )
            await debate_service.set_status(s, run_id=run_id, status="closed")
            await s.commit()

    writer = asyncio.create_task(add_a_post_then_finish())
    try:
        events = await _collect_stream(app_client, project_name, run_id)
    finally:
        await writer

    kinds = [json.loads(d)["kind"] for e, d in events if e == "post"]
    assert kinds == ["TASK", "PROPOSAL"], "the later post reached a connected client"
    assert events[-1][0] == "status"
    assert json.loads(events[-1][1])["status"] == "closed"


# --------------------------------------------------------------------------- #
# Opening a debate
#
# This route had no test, and it was the one that broke in production:
# `commit()` expires the ORM object, so reading `run.id` afterwards tried to
# refresh it and died with MissingGreenlet. Both tests below fail against that
# version.
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_opening_a_debate_returns_the_run(
    forum_config, app_client, session, alice, monkeypatch
):
    from vista_backend.api import debate as debate_api

    spawned: list[uuid.UUID] = []

    async def fake_task(run_id):
        spawned.append(run_id)

    monkeypatch.setattr(debate_api, "run_debate_task", fake_task)

    project = await _project(session, alice)
    resp = await app_client.post(
        f"/projects/{project.name}/debates",
        json={"topic": "why does the knee move?", "framing": "1 bar", "rounds": 2},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["topic"] == "why does the knee move?"
    assert body["rounds"] == 2
    assert body["status"] == "debating"
    assert body["thread_id"], "the forum thread has to exist before the response"

    # The task is scheduled, not run inline; one tick lets its body start.
    await asyncio.sleep(0)
    assert spawned == [uuid.UUID(body["id"])], "the argument is handed to a task"


@pytest.mark.anyio
async def test_opening_a_debate_attaches_the_whole_roster(
    forum_config, app_client, session, alice, monkeypatch
):
    from vista_backend.api import debate as debate_api

    async def fake_task(run_id):
        return None

    monkeypatch.setattr(debate_api, "run_debate_task", fake_task)

    project = await _project(session, alice)
    resp = await app_client.post(
        f"/projects/{project.name}/debates", json={"topic": "t", "rounds": 1}
    )
    assert resp.status_code == 200, resp.text
    run_id = uuid.UUID(resp.json()["id"])

    rows = await debate_service.list_participants(session, run_id=run_id)
    assert {r.debate_role for r in rows} == {"proposer", "reviewer", "referee"}
    assert all(r.policy_digest for r in rows)


@pytest.mark.anyio
async def test_opening_a_debate_is_503_when_the_forum_is_off(
    app_client, session, alice, monkeypatch
):
    """A deployment without h5i should say so, not fail deep in a subprocess."""
    monkeypatch.setattr(settings, "forum", ForumSettings(enabled=False))
    project = await _project(session, alice)

    resp = await app_client.post(
        f"/projects/{project.name}/debates", json={"topic": "t"}
    )
    assert resp.status_code == 503


# --------------------------------------------------------------------------- #
# Forum status
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_forum_status_reports_a_local_only_forum(forum_config, app_client):
    resp = await app_client.get("/forum/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] and not body["shared"]
    assert body["votes_counting"], "origin counts every machine's vote"


@pytest.mark.anyio
async def test_forum_status_says_when_votes_are_being_discarded(
    forum_config, app_client, monkeypatch
):
    """
    The state this endpoint exists for. `principal` with nobody enrolled throws
    away every vote on the forum — the agents' included — and nothing about a
    thread shows it. Silence here would mean a debate whose votes do nothing and
    an interface that never says so.
    """
    from vista_backend.config import settings
    from vista_backend.services.h5i_forum import ForumClient, VotePolicy

    client = ForumClient(settings.forum, confirm_delay=0.0)
    await client.set_vote_policy(VotePolicy.PRINCIPAL)

    resp = await app_client.get("/forum/status")
    body = resp.json()

    assert body["vote_policy"] == "principal"
    assert body["enrolled"] == 0
    assert body["votes_counting"] is False


@pytest.mark.anyio
async def test_forum_status_is_quiet_when_the_forum_is_off(app_client, monkeypatch):
    from vista_backend.config import ForumSettings, settings

    monkeypatch.setattr(settings, "forum", ForumSettings(enabled=False))
    body = (await app_client.get("/forum/status")).json()
    assert body == {
        "enabled": False,
        "shared": False,
        "remote": None,
        "vote_policy": None,
        "enrolled": 0,
        "votes_counting": True,
    }


@pytest.mark.anyio
async def test_forum_status_survives_an_unreadable_forum(
    forum_config, app_client, monkeypatch
):
    """An endpoint whose job is reporting bad states must not fail on one."""
    from vista_backend.config import ForumSettings, settings

    monkeypatch.setattr(
        settings,
        "forum",
        ForumSettings(
            enabled=True, binary="/nonexistent/h5i", repo_root=forum_config.repo_root
        ),
    )
    resp = await app_client.get("/forum/status")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
