"""
Tests for the debate API.

Real HTTP through an ASGI transport, a real DB session, and the in-memory fake
forum, so the routes, the access boundary and the event stream are all exercised. The
background argument is not started here — these test the surface a client sees,
and the loop has its own driver tests.

Routes are keyed by forum thread, not by this install's run: a thread may have
been opened by another install, and this install may never have argued in it.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import anyio
import httpx
import pytest

from harness.fake_forum import PEER_HOST_ID

from vista_backend.agents.forum.project_forum import build_client_for
from vista_backend.config import ForumSettings, settings
from vista_backend.db.schemas import ProjectCreate
from vista_backend.services import debate as debate_service, local_settings
from vista_backend.services import project as project_service
from vista_backend.services import threads
from vista_backend.services.forum_git import Participant
from vista_backend.services.git_check import GitCheck


@pytest.fixture
def forum_config(tmp_path, monkeypatch, fake_forum) -> ForumSettings:
    """
    Point the whole app at throwaway per-project forums, all of them the fake.

    `data_dir` and not `repo_root`: the repository is a property of the project
    now, so what a deployment configures is where project forums live, and each
    project's own root falls out of its id.
    """
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    config = ForumSettings(enabled=True, timeout=30.0)
    monkeypatch.setattr(settings, "forum", config)
    return config


@pytest.fixture
async def app_client(session, monkeypatch):
    """
    An HTTP client over the real app, sharing the test's DB session.

    The session override is what makes assertions possible — routes and test see
    the same uncommitted transaction, settings row included.
    """
    from vista_backend.api.api import app
    from vista_backend.db.db import _get_session

    async def _session():
        yield session

    app.dependency_overrides[_get_session] = _session

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
    # The refresh throttle outlives a test. Left alone, a test that reads a
    # thread right after another test refreshed it is told it was refreshed
    # moments ago and skips the read, which reads as "the peer's post never
    # arrived".
    monkeypatch.setattr(debate_api, "_last_refresh", {})
    monkeypatch.setattr(debate_api, "_refresh_locks", {})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


FORUM_URL = "https://example.invalid/forum.git"


async def _project(session, name="api-debate", forum_repo_url=FORUM_URL):
    """
    A project with a Hypothesis Lab, unless the caller asks for one without.

    Not through `ensure_forum`, which would run git and sync: these tests are
    about the API, and the setup path has its own file.
    """
    return await project_service.create_project(
        session, ProjectCreate(name=name, forum_repo_url=forum_repo_url)
    )


async def _run(session, project, *, topic="why does the knee move?"):
    """
    A debate row with a real forum thread behind it, and the thread's own row
    linked to it, the way opening a thread leaves them.
    """
    client = build_client_for(project, await local_settings.get(session))
    thread_id = await client.create_thread(topic, body="Debate it.")
    run = await debate_service.create_debate(
        session,
        project_id=project.id,
        topic=topic,
        thread_id=thread_id,
        framing="Debate it.",
        rounds=2,
    )
    thread = await threads.ensure_thread(
        session,
        project_id=project.id,
        thread_id=thread_id,
        title=topic,
        framing="Debate it.",
        created_by=client.host_id,
    )
    await threads.link_run(session, run=run, thread=thread)
    participant = Participant(
        identity=f"vista-proposer-{str(run.id)[:8]}", role="proposer"
    )
    await debate_service.add_participant(
        session, run_id=run.id, participant=participant, debate_role="proposer"
    )
    await debate_service.set_status(session, run_id=run.id, status="debating")
    return run, client, participant


async def _refresh(session, project_id, thread_id, client):
    """Fold the forum's copy of a thread into the tables, as a watcher would."""
    from vista_backend.api import debate as debate_api

    row = await threads.require_thread(
        session, project_id=project_id, thread_id=thread_id
    )
    await debate_api.refresh_thread(session, row, client)


def _hour_ago() -> str:
    return (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="microseconds")


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_get_picks_up_a_peer_post_on_a_finished_debate(
    forum_config, app_client, session
):
    """
    The case federation exists for, and the one that was invisible.

    An outside reviewer is most likely to comment *after* a debate has argued
    itself out — that is when there is a hypothesis worth objecting to. But a
    finished run is not in ACTIVE_STATUSES, so the UI opens no event stream for
    it, and the refresh that reads the forum lived only inside that stream. With
    the plain GET reading nothing but the local projection, a peer's post could
    never appear: not late, never.
    """
    project = await _project(session)
    run, client, _ = await _run(session, project)
    await _refresh(session, project.id, run.thread_id, client)
    # Read before committing: a commit expires every object the session holds,
    # and touching one afterwards is async IO where none can be awaited.
    run_id, thread_id, project_name = run.id, run.thread_id, project.name
    await debate_service.set_status(session, run_id=run_id, status="converged")
    await session.commit()

    # A peer publishes to the remote; nothing local knows yet.
    client.peer_post(thread_id, "the 803 K figure is from a fit", kind="FINDING")

    body = (
        await app_client.get(f"/projects/{project_name}/threads/{thread_id}")
    ).json()

    assert "FINDING" in [p["kind"] for p in body["posts"]], (
        "a peer commented on a finished debate and the interface never showed it"
    )


@pytest.mark.anyio
async def test_a_failed_refresh_still_returns_the_debate(
    forum_config, app_client, session, monkeypatch
):
    """
    A refresh that fails is logged and survived — the read still answers.

    The failure path rolls back, and a rollback expires the caller's `run` just as
    a commit does. Only the commit path re-read it, so the first view of a new
    debate whose refresh lost a race serialised an expired row and was a 500.
    """
    from vista_backend.api import debate as debate_api

    project = await _project(session)
    run, client, _ = await _run(session, project)
    await _refresh(session, project.id, run.thread_id, client)
    thread_id, project_name = run.thread_id, project.name
    await session.commit()

    async def broken(*_args, **_kwargs):
        raise RuntimeError("the forum read failed")

    monkeypatch.setattr(debate_api, "refresh_thread", broken)

    resp = await app_client.get(f"/projects/{project_name}/threads/{thread_id}")

    assert resp.status_code == 200
    assert resp.json()["thread"]["title"] == "why does the knee move?"
    assert resp.json()["thread"]["run"]["topic"] == "why does the knee move?"


@pytest.mark.anyio
async def test_get_returns_run_participants_and_posts(
    forum_config, app_client, session
):
    project = await _project(session)
    run, client, _ = await _run(session, project)
    await _refresh(session, project.id, run.thread_id, client)
    thread_id, project_name = run.thread_id, project.name

    resp = await app_client.get(f"/projects/{project_name}/threads/{thread_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["thread"]["thread_id"] == thread_id
    assert body["thread"]["opened_here"] is True
    assert body["thread"]["run"]["topic"] == "why does the knee move?"
    assert body["thread"]["run"]["status"] == "debating"
    assert [p["debate_role"] for p in body["participants"]] == ["proposer"]
    assert [p["kind"] for p in body["posts"]] == ["TASK"]


@pytest.mark.anyio
async def test_the_post_payload_keeps_provenance_visible(
    forum_config, app_client, session
):
    """
    The UI has to be able to draw the known/claimed boundary, so the API must
    not flatten it into one blob of text.
    """
    project = await _project(session)
    run, client, participant = await _run(session, project)
    await client.post_as(
        participant, run.thread_id, "rigidity sets the knee", kind="PROPOSAL"
    )
    await _refresh(session, project.id, run.thread_id, client)
    thread_id, project_name = run.thread_id, project.name

    resp = await app_client.get(f"/projects/{project_name}/threads/{thread_id}")
    proposal = next(p for p in resp.json()["posts"] if p["kind"] == "PROPOSAL")

    assert proposal["body"] == "rigidity sets the knee"
    for field in ("sender", "forum_role", "origin"):
        assert proposal[field], f"{field} must reach the client"
    assert proposal["vouch_lane"] == "host-observed"
    # No remote was set on this forum, so our post is honestly not yet published.
    assert proposal["published"] is False


@pytest.mark.anyio
async def test_list_is_scoped_to_the_project(forum_config, app_client, session):
    mine = await _project(session, name="mine")
    theirs = await _project(session, name="theirs")
    run, _, _ = await _run(session, mine)
    run_id, thread_id = run.id, run.thread_id
    mine_name, theirs_name = mine.name, theirs.name

    resp = await app_client.get(f"/projects/{mine_name}/threads")
    body = resp.json()
    assert [t["thread_id"] for t in body] == [thread_id]
    assert body[0]["run"]["id"] == str(run_id)
    resp = await app_client.get(f"/projects/{theirs_name}/threads")
    assert resp.json() == []


@pytest.mark.anyio
async def test_a_thread_another_install_opened_is_listed(
    forum_config, app_client, session
):
    """
    The lab lists every thread on the forum, whoever opened it. A peer's thread
    is shown as theirs: this install did not open it and has not argued in it.
    """
    project = await _project(session)
    project_name = project.name
    client = build_client_for(project, await local_settings.get(session))
    thread_id = client.peer_thread("is the knee a glass transition?", body="Argue.")

    resp = await app_client.get(f"/projects/{project_name}/threads")
    assert resp.status_code == 200
    listed = next((t for t in resp.json() if t["thread_id"] == thread_id), None)
    assert listed is not None, "the peer's thread never reached the list"
    assert listed["opened_here"] is False
    assert listed["created_by"] == PEER_HOST_ID
    assert listed["title"] == "is the knee a glass transition?"
    assert listed["framing"] == "Argue."
    assert listed["run"] is None


# --------------------------------------------------------------------------- #
# The access boundary
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_debate_is_not_readable_through_another_project(
    forum_config, app_client, session
):
    """The project is the boundary; the thread id alone must not be enough."""
    mine = await _project(session, name="mine")
    other = await _project(session, name="other")
    run, _, _ = await _run(session, mine)

    resp = await app_client.get(f"/projects/{other.name}/threads/{run.thread_id}")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_an_unknown_debate_is_404(forum_config, app_client, session):
    project = await _project(session)
    resp = await app_client.get(f"/projects/{project.name}/threads/{uuid.uuid7()}")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# The human joining in
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_human_can_post_into_a_live_debate(forum_config, app_client, session):
    project = await _project(session)
    run, client, _ = await _run(session, project)
    # The route commits, which expires `run`.
    thread_id = run.thread_id

    resp = await app_client.post(
        f"/projects/{project.name}/threads/{thread_id}/posts",
        json={"body": "constrain this to 1 bar", "kind": "ASK"},
    )
    assert resp.status_code == 200
    post = resp.json()
    assert post["sender"] == "human", "the human posts as themselves, not as an agent"
    assert post["kind"] == "ASK"
    assert post["round_index"] is None, "an interjection belongs to no agent round"

    thread = await client.read_thread(thread_id)
    assert "constrain this to 1 bar" in [p.body for p in thread.posts]


@pytest.mark.anyio
async def test_the_human_can_post_into_a_peers_thread(
    forum_config, app_client, session
):
    """
    Saying something into a thread needs no run of this install's: a teammate's
    thread takes the researcher's post like one of our own.
    """
    project = await _project(session)
    project_name = project.name
    client = build_client_for(project, await local_settings.get(session))
    thread_id = client.peer_thread("a peer's question", body="Argue.")
    await app_client.get(f"/projects/{project_name}/threads")  # the sync finds it

    resp = await app_client.post(
        f"/projects/{project_name}/threads/{thread_id}/posts",
        json={"body": "what about 2 bar?", "kind": "ASK"},
    )
    assert resp.status_code == 200, resp.text
    post = resp.json()
    assert post["sender"] == "human"
    assert post["run_id"] is None, "no run of ours wrote it"
    assert post["vouch_lane"] == "host-observed"
    thread = await client.read_thread(thread_id)
    assert "what about 2 bar?" in [p.body for p in thread.posts]


@pytest.mark.anyio
async def test_a_closed_thread_refuses_the_humans_post_too(
    forum_config, app_client, session
):
    """
    Readers ignore everything after the first CLOSED, so a post there would be
    written and never shown. Saying no is the honest answer.
    """
    project = await _project(session)
    run, client, _ = await _run(session, project)
    await client.close_thread(run.thread_id)

    resp = await app_client.post(
        f"/projects/{project.name}/threads/{run.thread_id}/posts",
        json={"body": "for the record: we stopped because of the shear data"},
    )
    assert resp.status_code == 409
    assert "closed" in resp.json()["detail"]


@pytest.mark.anyio
async def test_an_unpostable_kind_is_rejected(forum_config, app_client, session):
    """
    `CLOSED` is a real kind, written only by closing; `CLAIM` is not a kind at
    all. Neither may be posted.
    """
    project = await _project(session)
    run, _, _ = await _run(session, project)

    for kind in ("CLAIM", "CLOSED", "UPVOTE", "RUN"):
        resp = await app_client.post(
            f"/projects/{project.name}/threads/{run.thread_id}/posts",
            json={"body": "mine now", "kind": kind},
        )
        assert resp.status_code == 422, kind


# --------------------------------------------------------------------------- #
# Closing
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_opening_a_debate_with_no_inference_key_says_where_to_add_one(
    forum_config, app_client, session, monkeypatch
):
    """
    With no key anywhere, opening a debate is the same named condition as a
    chat: 409 and the settings location, not a 500 from deep in the provider.
    Nothing is opened on the forum either, since the debate could not argue.
    """
    from vista_backend.agents.inference import SETTINGS_LOCATION

    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "model", "openai:deployment-model")
    project = await _project(session)

    resp = await app_client.post(
        f"/projects/{project.name}/threads", json={"topic": "why does the knee move?"}
    )

    assert resp.status_code == 409, resp.text
    assert SETTINGS_LOCATION in resp.json()["detail"]
    assert await debate_service.list_debates(session, project_id=project.id) == []
    assert await threads.list_threads(session, project_id=project.id) == []


@pytest.mark.anyio
async def test_continuing_a_debate_without_a_key_says_where_to_add_one(
    forum_config, app_client, session, monkeypatch
):
    """
    More rounds run on the installation's settings, so with no key there the request
    is refused up front, rather than accepted and then failing in a background
    task where nobody sees why.
    """
    from vista_backend.agents.inference import SETTINGS_LOCATION

    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "model", "openai:deployment-model")
    project = await _project(session)
    run, _, _ = await _run(session, project)
    run_id, thread_id, project_name = run.id, run.thread_id, project.name
    await debate_service.set_status(session, run_id=run_id, status="converged")

    resp = await app_client.post(
        f"/projects/{project_name}/threads/{thread_id}/runs", json={"rounds": 1}
    )

    assert resp.status_code == 409, resp.text
    assert SETTINGS_LOCATION in resp.json()["detail"]
    assert (await debate_service.require_debate(session, run_id)).status == "converged"


@pytest.mark.anyio
async def test_closing_ends_the_debate_on_the_forum_too(
    forum_config, app_client, session
):
    project = await _project(session)
    run, client, _ = await _run(session, project)
    run_id, thread_id = run.id, run.thread_id

    resp = await app_client.post(f"/projects/{project.name}/threads/{thread_id}/close")
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"
    assert resp.json()["run"]["status"] == "closed", "our live run ends with it"
    assert (await client.read_thread(thread_id)).is_closed
    assert (await debate_service.require_debate(session, run_id)).status == "closed"


@pytest.mark.anyio
async def test_only_the_opener_can_close_a_thread(forum_config, app_client, session):
    """
    Closing ends a thread for everyone, so it is the opener's call. Another
    install's thread is refused, and nothing is written to the forum.
    """
    project = await _project(session)
    project_name = project.name
    client = build_client_for(project, await local_settings.get(session))
    thread_id = client.peer_thread("a peer's question", body="Argue.")
    await app_client.get(f"/projects/{project_name}/threads")  # the sync finds it

    resp = await app_client.post(f"/projects/{project_name}/threads/{thread_id}/close")

    assert resp.status_code == 403, resp.text
    thread = await client.read_thread(thread_id)
    assert not thread.is_closed
    assert "CLOSED" not in [p.kind for p in thread.posts]


# --------------------------------------------------------------------------- #
# The stream
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_stream_replays_the_thread_then_ends_when_it_is_closed(
    forum_config, app_client, session
):
    """
    A closed thread's stream is finite: every post once, then the thread, then
    the connection closes. A stream that hung after the close would leave every
    client waiting on a thread that ended.
    """
    project = await _project(session)
    run, client, participant = await _run(session, project)
    await client.post_as(participant, run.thread_id, "claim", kind="PROPOSAL")
    await client.close_thread(run.thread_id)
    await _refresh(session, project.id, run.thread_id, client)
    thread_id, project_name = run.thread_id, project.name

    events = await _collect_stream(app_client, project_name, thread_id)
    kinds = [json.loads(d)["kind"] for e, d in events if e == "post"]
    assert kinds == ["TASK", "PROPOSAL", "CLOSED"]

    assert events[-1][0] == "thread"
    assert json.loads(events[-1][1])["status"] == "closed"


@pytest.mark.anyio
async def test_the_stream_is_scoped_to_the_project(forum_config, app_client, session):
    mine = await _project(session, name="mine")
    other = await _project(session, name="other")
    run, _, _ = await _run(session, mine)

    resp = await app_client.get(
        f"/projects/{other.name}/threads/{run.thread_id}/events"
    )
    assert resp.status_code == 404


async def _collect_stream(client, project_name, thread_id, limit=50, timeout=10.0):
    """
    Read an SSE response into (event, data) pairs.

    Bounded by a timeout on purpose. The stream ends itself when the thread is
    closed, so a thread that never reads as closed means a bug — and without
    this, that bug presents as a suite that hangs rather than a test that
    fails. (Removing the stream's between-poll rollback does exactly that: it
    re-reads its first snapshot forever and never sees the close.)
    """

    async def read() -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        async with client.stream(
            "GET", f"/projects/{project_name}/threads/{thread_id}/events"
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
            "it is not seeing the thread closed"
        )
    return []


async def _close_in(engine, client, project_id, thread_id):
    """Close the thread on the forum and fold that in, on a session of its own."""
    from sqlmodel.ext.asyncio.session import AsyncSession

    await client.close_thread(thread_id)
    async with AsyncSession(engine) as s:
        await _refresh(s, project_id, thread_id, client)
        await s.commit()


@pytest.mark.anyio
async def test_the_stream_emits_posts_that_arrive_while_it_is_open(
    forum_config, app_client, session, engine, monkeypatch
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

    project = await _project(session)
    run, client, participant = await _run(session, project)
    await _refresh(session, project.id, run.thread_id, client)
    # Read these out before committing: `commit()` expires the ORM object, and a
    # later attribute access from another task would try to refresh it there.
    project_id, thread_id, project_name = project.id, run.thread_id, project.name
    await session.commit()

    monkeypatch.setattr(
        debate_api, "stream_session_factory", lambda: AsyncSession(engine)
    )

    async def add_a_post_then_close():
        await asyncio.sleep(0.05)
        await client.post_as(
            participant, thread_id, "rigidity sets it", kind="PROPOSAL"
        )
        await _close_in(engine, client, project_id, thread_id)

    writer = asyncio.create_task(add_a_post_then_close())
    try:
        events = await _collect_stream(app_client, project_name, thread_id)
    finally:
        await writer

    kinds = [json.loads(d)["kind"] for e, d in events if e == "post"]
    assert kinds == ["TASK", "PROPOSAL", "CLOSED"], (
        "the later post reached a connected client"
    )
    assert events[-1][0] == "thread"
    assert json.loads(events[-1][1])["status"] == "closed"


@pytest.mark.anyio
async def test_the_stream_resends_a_post_whose_provenance_lands_late(
    forum_config, app_client, session, engine, monkeypatch
):
    """
    A post's tools are written after the post exists, so the stream has to revise.

    `_post` publishes to the forum, reads the thread back to create the row, and
    only then writes the provenance — and the forum refresh the stream does for
    peer comments can create that row first, from a record that has no field for
    how the agent got there. Sending each post once left it reading "based on
    model alone" for the whole connection, correcting only when the run ended and
    the client went back to fetching whole states.

    The client has always replaced on a matching `post_id` — its own comment says
    a replayed post can carry a newer tally — so the missing half was here.
    """
    from sqlmodel.ext.asyncio.session import AsyncSession

    from vista_backend.api import debate as debate_api

    project = await _project(session)
    run, client, participant = await _run(session, project)
    post = await client.post_as(participant, run.thread_id, "rigidity", kind="PROPOSAL")
    await _refresh(session, project.id, run.thread_id, client)
    run_id, project_id, thread_id = run.id, project.id, run.thread_id
    project_name, post_id = project.name, post.id
    await session.commit()

    monkeypatch.setattr(
        debate_api, "stream_session_factory", lambda: AsyncSession(engine)
    )

    async def record_the_tools_then_close():
        await asyncio.sleep(0.05)
        async with AsyncSession(engine) as s:
            await debate_service.record_post_tools(
                s,
                run_id=run_id,
                post_id=post_id,
                tools=[{"tool": "search_literature", "detail": "FLiBe viscosity"}],
            )
            await s.commit()
        await asyncio.sleep(0.05)
        await _close_in(engine, client, project_id, thread_id)

    writer = asyncio.create_task(record_the_tools_then_close())
    try:
        events = await _collect_stream(app_client, project_name, thread_id)
    finally:
        await writer

    versions = [
        json.loads(d)
        for e, d in events
        if e == "post" and json.loads(d)["kind"] == "PROPOSAL"
    ]
    assert len(versions) == 2, "the post is sent again once its provenance lands"
    assert versions[0]["tools_used"] == [], "first sighting: the row had no tools yet"
    assert versions[-1]["tools_used"][0]["tool"] == "search_literature"


@pytest.mark.anyio
async def test_the_stream_does_not_resend_an_unchanged_post(
    forum_config, app_client, session, engine, monkeypatch
):
    """
    The fingerprint has to be the payload, not the poll.

    Re-emitting on every pass would work and would also push a post a second
    every time anyone watches a live debate — a fix that trades a stale field for
    a flood.
    """
    from sqlmodel.ext.asyncio.session import AsyncSession

    from vista_backend.api import debate as debate_api

    project = await _project(session)
    run, client, _participant = await _run(session, project)
    await _refresh(session, project.id, run.thread_id, client)
    project_id, thread_id, project_name = project.id, run.thread_id, project.name
    await session.commit()

    monkeypatch.setattr(
        debate_api, "stream_session_factory", lambda: AsyncSession(engine)
    )

    # Counted, not timed. Sleeping "a few poll intervals" and hoping looks like a
    # test and is not one: with the fixture's 0.01s interval the stream got
    # through a single pass before the run closed, so re-emitting on every poll
    # left this green.
    polls = 0
    real_posts_with_notes = threads.posts_with_notes

    async def counting_posts_with_notes(session, **kwargs):
        nonlocal polls
        polls += 1
        return await real_posts_with_notes(session, **kwargs)

    monkeypatch.setattr(threads, "posts_with_notes", counting_posts_with_notes)

    async def close_once_it_has_polled_enough():
        while polls < 4:
            await asyncio.sleep(debate_api.STREAM_POLL_SECONDS / 2)
        await _close_in(engine, client, project_id, thread_id)

    writer = asyncio.create_task(close_once_it_has_polled_enough())
    try:
        events = await _collect_stream(app_client, project_name, thread_id)
    finally:
        await writer

    assert polls >= 4, "the stream has to have looked more than once"
    posts = [json.loads(d) for e, d in events if e == "post"]
    assert [p["kind"] for p in posts] == ["TASK", "CLOSED"], (
        "an unchanged post is sent exactly once"
    )


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
    forum_config, app_client, session, monkeypatch
):
    from vista_backend.api import debate as debate_api

    spawned: list[uuid.UUID] = []

    async def fake_task(run_id):
        spawned.append(run_id)

    monkeypatch.setattr(debate_api, "run_debate_task", fake_task)

    project = await _project(session)
    resp = await app_client.post(
        f"/projects/{project.name}/threads",
        json={"topic": "why does the knee move?", "framing": "1 bar", "rounds": 2},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "why does the knee move?"
    assert body["opened_here"] is True
    assert body["thread_id"], "the forum thread has to exist before the response"
    assert body["run"]["topic"] == "why does the knee move?"
    assert body["run"]["rounds"] == 2
    assert body["run"]["status"] == "debating"
    assert body["run"]["thread_id"] == body["thread_id"]

    # The task is scheduled, not run inline; one tick lets its body start.
    await asyncio.sleep(0)
    assert spawned == [uuid.UUID(body["run"]["id"])], "the argument is handed to a task"


@pytest.mark.anyio
async def test_opening_a_debate_attaches_the_whole_roster(
    forum_config, app_client, session, monkeypatch
):
    from vista_backend.api import debate as debate_api

    async def fake_task(run_id):
        return None

    monkeypatch.setattr(debate_api, "run_debate_task", fake_task)

    project = await _project(session)
    resp = await app_client.post(
        f"/projects/{project.name}/threads", json={"topic": "t", "rounds": 1}
    )
    assert resp.status_code == 200, resp.text
    run_id = uuid.UUID(resp.json()["run"]["id"])

    rows = await debate_service.list_participants(session, run_id=run_id)
    assert {r.debate_role for r in rows} == {"proposer", "reviewer", "referee"}
    assert all(r.identity.endswith(str(run_id)[:8]) for r in rows)


@pytest.mark.anyio
async def test_opening_a_debate_is_503_when_the_forum_is_off(
    app_client, session, monkeypatch
):
    """A deployment with the forum off should say so, not fail deep in git."""
    monkeypatch.setattr(settings, "forum", ForumSettings(enabled=False))
    project = await _project(session)

    resp = await app_client.post(
        f"/projects/{project.name}/threads", json={"topic": "t"}
    )
    assert resp.status_code == 503


# --------------------------------------------------------------------------- #
# Continuing
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_concluded_debate_a_peer_closed_cannot_be_continued(
    forum_config, app_client, session
):
    """
    Found in the two-install check (task 7.2). A converged run keeps its status
    when a peer closes the thread afterwards, so the CLOSED post is what has to
    refuse more rounds — the status alone would let a roster start that could
    not post.
    """
    project = await _project(session)
    run, client, _ = await _run(session, project)
    run_id, thread_id, project_name = run.id, run.thread_id, project.name
    await debate_service.set_status(session, run_id=run_id, status="converged")
    await session.commit()
    client.peer_close(thread_id)

    body = (
        await app_client.get(f"/projects/{project_name}/threads/{thread_id}")
    ).json()
    assert body["thread"]["run"]["status"] == "converged", (
        "a verdict is not undone by a close"
    )
    assert body["thread"]["status"] == "closed"
    assert body["posts"][-1]["kind"] == "CLOSED"

    resp = await app_client.post(
        f"/projects/{project_name}/threads/{thread_id}/runs", json={"rounds": 1}
    )
    assert resp.status_code == 409
    post = await app_client.post(
        f"/projects/{project_name}/threads/{thread_id}/posts", json={"body": "hi"}
    )
    assert post.status_code == 409


@pytest.mark.anyio
async def test_continue_raises_the_budget_and_spawns_the_argument(
    forum_config, app_client, session, monkeypatch
):
    from vista_backend.api import debate as debate_api

    spawned: list[tuple[uuid.UUID, int]] = []
    started = asyncio.Event()

    async def fake_task(run_id, extra_rounds):
        spawned.append((run_id, extra_rounds))
        started.set()

    monkeypatch.setattr(debate_api, "continue_debate_task", fake_task)

    project = await _project(session)
    run, _, _ = await _run(session, project)
    run_id, thread_id, project_name = run.id, run.thread_id, project.name
    await debate_service.set_status(session, run_id=run_id, status="converged")
    await session.commit()

    resp = await app_client.post(
        f"/projects/{project_name}/threads/{thread_id}/runs", json={"rounds": 3}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["run"]["id"] == str(run_id), "our own run is the one resumed"
    # The endpoint returns as soon as the task is scheduled; it has not run yet.
    with anyio.fail_after(2):
        await started.wait()
    assert spawned == [(run_id, 3)]

    # The budget is raised rather than reset, so the record still says how much
    # arguing this debate has had in total.
    refreshed = await debate_service.require_debate(session, run_id)
    assert refreshed.rounds == 2, "the orchestrator raises it, not the endpoint"


@pytest.mark.anyio
async def test_continue_is_refused_while_the_debate_is_still_arguing(
    forum_config, app_client, session
):
    """Two orchestrators on one thread would interleave turns and both be wrong."""
    project = await _project(session)
    run, _, _ = await _run(session, project)  # left at "debating"

    resp = await app_client.post(
        f"/projects/{project.name}/threads/{run.thread_id}/runs", json={"rounds": 2}
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "This debate is still arguing."


@pytest.mark.anyio
async def test_continue_is_refused_on_a_closed_thread(
    forum_config, app_client, session
):
    """
    A closed thread accepts no posts. Continuing would start a roster that
    could not speak, then fail a round in.
    """
    project = await _project(session)
    run, client, _ = await _run(session, project)
    run_id, thread_id, project_name = run.id, run.thread_id, project.name
    await client.close_thread(thread_id)
    await debate_service.set_status(session, run_id=run_id, status="closed")
    await session.commit()

    resp = await app_client.post(
        f"/projects/{project_name}/threads/{thread_id}/runs", json={"rounds": 2}
    )
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_an_unknown_thread_cannot_be_continued(forum_config, app_client, session):
    project = await _project(session)
    resp = await app_client.post(
        f"/projects/{project.name}/threads/{uuid.uuid7()}/runs", json={"rounds": 1}
    )
    assert resp.status_code == 404


@pytest.fixture
def inference_key(monkeypatch):
    """
    An inference key from `Settings`, so a role's own model passes the guard.

    A role model is an `openai:` name on the configured endpoint, so without a
    key it is refused with a 409 -- which is the guard's job, tested elsewhere,
    and not what these tests are about. Supplied here rather than inherited:
    a developer's own `.env` above the checkout would otherwise provide one.
    """
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "openai_api_key", SecretStr("test-key"))


@pytest.fixture
def no_argument(monkeypatch) -> list[tuple[uuid.UUID, int]]:
    """Continuing schedules nothing that argues; the calls are recorded."""
    from vista_backend.api import debate as debate_api

    spawned: list[tuple[uuid.UUID, int]] = []

    async def fake_task(run_id, extra_rounds):
        spawned.append((run_id, extra_rounds))

    monkeypatch.setattr(debate_api, "continue_debate_task", fake_task)
    return spawned


@pytest.mark.anyio
async def test_continuing_a_peers_thread_adopts_it(
    forum_config, app_client, session, inference_key, no_argument
):
    """
    A thread this install never argued in can still be argued: it is adopted
    first, with a run and a roster of this install's own.
    """
    project = await _project(session)
    project_name = project.name
    client = build_client_for(project, await local_settings.get(session))
    thread_id = client.peer_thread("a peer's question", body="Argue.")
    await app_client.get(f"/projects/{project_name}/threads")  # the sync finds it

    resp = await app_client.post(
        f"/projects/{project_name}/threads/{thread_id}/runs", json={"rounds": 2}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["opened_here"] is False, "adopting does not make it ours"
    run_id = resp.json()["run"]["id"]
    assert run_id

    await asyncio.sleep(0)
    assert no_argument == [(uuid.UUID(run_id), 2)]

    body = (
        await app_client.get(f"/projects/{project_name}/threads/{thread_id}")
    ).json()
    assert body["thread"]["run"]["id"] == run_id
    assert body["thread"]["run"]["thread_id"] == thread_id
    assert {p["debate_role"] for p in body["participants"]} == {
        "proposer",
        "reviewer",
        "referee",
    }
    assert all(p["identity"].endswith(run_id[:8]) for p in body["participants"]), (
        "the adopted roster is ours, told apart from the opener's"
    )


@pytest.mark.anyio
async def test_continue_is_refused_while_a_peers_run_is_live(
    forum_config, app_client, session, inference_key, no_argument
):
    """
    One run at a time per thread, whoever's it is: a teammate's live RUN
    refuses ours, and says that it is someone else's. A RUN gone quiet for
    longer than the expiry no longer holds the thread.
    """
    project = await _project(session)
    project_name = project.name
    client = build_client_for(project, await local_settings.get(session))
    live = client.peer_thread("a live one", body="Argue.")
    client.peer_run(live)
    stale = client.peer_thread("a stale one", body="Argue.")
    client.peer_run(stale, ts=_hour_ago())
    assert settings.forum.run_expiry_seconds < 3600, "an hour old must be expired"
    await app_client.get(f"/projects/{project_name}/threads")

    listed = {
        t["thread_id"]: t
        for t in (await app_client.get(f"/projects/{project_name}/threads")).json()
    }
    assert listed[live]["live_run"] is not None
    assert listed[live]["live_run"]["ours"] is False
    assert listed[stale]["live_run"] is None

    resp = await app_client.post(
        f"/projects/{project_name}/threads/{live}/runs", json={"rounds": 1}
    )
    assert resp.status_code == 409, resp.text
    assert "Another researcher" in resp.json()["detail"]
    assert no_argument == []

    resp = await app_client.post(
        f"/projects/{project_name}/threads/{stale}/runs", json={"rounds": 1}
    )
    assert resp.status_code == 200, resp.text
    await asyncio.sleep(0)
    assert len(no_argument) == 1


@pytest.mark.anyio
async def test_continue_changes_only_the_roles_it_names(
    forum_config, app_client, session, monkeypatch, inference_key
):
    from vista_backend.api import debate as debate_api

    async def fake_task(run_id, extra_rounds):
        return None

    monkeypatch.setattr(debate_api, "continue_debate_task", fake_task)

    project = await _project(session)
    run, _, _ = await _run(session, project)
    run_id, thread_id, project_name = run.id, run.thread_id, project.name
    for role, model in (("reviewer", None), ("referee", "openai:referee-b")):
        await debate_service.add_participant(
            session,
            run_id=run_id,
            participant=Participant(identity=f"vista-{role}-x", role=role),
            debate_role=role,
            model=model,
        )
    await debate_service.set_status(session, run_id=run_id, status="converged")
    await session.commit()

    async def models():
        rows = await debate_service.list_participants(session, run_id=run_id)
        return {r.debate_role: r.model for r in rows}

    resp = await app_client.post(
        f"/projects/{project_name}/threads/{thread_id}/runs",
        json={"rounds": 1, "models": {"reviewer": "openai:reviewer-c"}},
    )
    assert resp.status_code == 200, resp.text
    assert await models() == {
        "proposer": None,
        "reviewer": "openai:reviewer-c",
        "referee": "openai:referee-b",
    }

    # Untouched form: nothing changes. An explicit null returns a role to the default.
    await debate_service.set_status(session, run_id=run_id, status="converged")
    await session.commit()
    resp = await app_client.post(
        f"/projects/{project_name}/threads/{thread_id}/runs",
        json={"rounds": 1, "models": {"referee": None}},
    )
    assert resp.status_code == 200, resp.text
    assert await models() == {
        "proposer": None,
        "reviewer": "openai:reviewer-c",
        "referee": None,
    }


# --------------------------------------------------------------------------- #
# Who said it
# --------------------------------------------------------------------------- #


async def _name_the_researcher(session, name="Ada Researcher") -> str:
    (await local_settings.get(session)).display_name = name
    await session.flush()
    return name


@pytest.mark.anyio
async def test_a_human_post_records_the_researcher_who_wrote_it(
    forum_config, app_client, session
):
    """
    The forum has nowhere to put a name: every operator's post says
    `sender="human"`, which is why a peer's post looks just like ours. But
    *this* post was made at this installation, so who wrote it is knowledge
    here even though the forum cannot carry it.
    """
    name = await _name_the_researcher(session)
    project = await _project(session)
    run, _, _ = await _run(session, project)

    resp = await app_client.post(
        f"/projects/{project.name}/threads/{run.thread_id}/posts",
        json={"body": "what about the beryllium supply?", "kind": "ASK"},
    )
    assert resp.status_code == 200
    assert resp.json()["authored_by"] == name


@pytest.mark.anyio
async def test_a_human_post_names_no_author_when_settings_name_none(
    forum_config, app_client, session
):
    """
    Without a name in Settings there is nobody to record. The email is not a
    fallback: it is for notifications, and nothing says it would be shown.
    """
    (await local_settings.get(session)).email = "ada@example.org"
    await session.flush()
    project = await _project(session)
    run, _, _ = await _run(session, project)

    resp = await app_client.post(
        f"/projects/{project.name}/threads/{run.thread_id}/posts",
        json={"body": "what about the beryllium supply?", "kind": "ASK"},
    )
    assert resp.status_code == 200
    assert resp.json()["authored_by"] is None


@pytest.mark.anyio
async def test_the_author_survives_the_thread_being_replayed(
    forum_config, app_client, session
):
    """
    The projection re-reads the whole thread on every refresh. If that path
    rebuilt rows wholesale it would erase the one field the forum cannot supply,
    and the name would vanish the first time a peer commented.
    """
    name = await _name_the_researcher(session)
    project = await _project(session)
    run, client, _ = await _run(session, project)
    project_id, thread_id = project.id, run.thread_id
    await app_client.post(
        f"/projects/{project.name}/threads/{thread_id}/posts",
        json={"body": "and the corrosion data?", "kind": "ASK"},
    )

    client.peer_post(thread_id, "a peer's comment")
    await _refresh(session, project_id, thread_id, client)

    row = await threads.require_thread(
        session, project_id=project_id, thread_id=thread_id
    )
    posts = await threads.posts_with_notes(session, thread=row)
    asked = next(p for p in posts if p.kind == "ASK")
    assert asked.authored_by == name


@pytest.mark.anyio
async def test_a_peer_post_is_never_given_an_author(forum_config, app_client, session):
    """
    The distinction the whole feature rests on. A peer's post arrives as
    `sender="human"` exactly like ours, and there is nothing in it we know, so
    the post row stays anonymous.
    """
    await _name_the_researcher(session)
    project = await _project(session)
    run, client, _ = await _run(session, project)
    # Arrived over the remote: this is what someone else's install looks like.
    client.peer_post(
        run.thread_id, "the fit is not a measurement", identity="human", role="human"
    )

    body = (
        await app_client.get(f"/projects/{project.name}/threads/{run.thread_id}")
    ).json()

    posted = next((p for p in body["posts"] if "not a measurement" in p["body"]), None)
    assert posted is not None, "the peer's post never reached the projection"
    assert posted["authored_by"] is None


# --------------------------------------------------------------------------- #
# Forum status
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_forum_status_reports_a_local_only_forum(
    forum_config, app_client, session
):
    project = await _project(session)
    resp = await app_client.get(f"/projects/{project.name}/forum/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] and not body["shared"]
    assert body["git_ok"] and body["git_reason"] is None
    assert body["unpublished"] == 0


@pytest.mark.parametrize(
    "error",
    [
        None,
        {
            "reason": "refused",
            "credential": "settings-github",
            "message": "Repository not found",
            "at": "t",
        },
        {
            "reason": "unreachable",
            "credential": "system-git",
            "message": "Could not resolve host",
            "at": "t",
        },
        {
            "reason": "failed",
            "credential": "system-git",
            "message": "something else",
            "at": "t",
        },
    ],
)
@pytest.mark.anyio
async def test_forum_status_says_why_publishing_failed(
    forum_config, app_client, session, error
):
    project = await _project(session)
    client = build_client_for(project, await local_settings.get(session))
    client.forum.publish_error = error

    body = (await app_client.get(f"/projects/{project.name}/forum/status")).json()

    assert body["publish_error"] == error


@pytest.mark.anyio
async def test_an_unreadable_failure_record_is_no_record(
    forum_config, app_client, session
):
    """The status route reports bad states; a record it cannot parse must not be one."""
    project = await _project(session)
    client = build_client_for(project, await local_settings.get(session))
    client.forum.publish_error = {
        "reason": "mystery",
        "credential": "?",
        "message": "",
        "at": "",
    }

    resp = await app_client.get(f"/projects/{project.name}/forum/status")

    assert resp.status_code == 200
    assert resp.json()["publish_error"] is None


@pytest.mark.anyio
async def test_a_failure_naming_the_projects_old_token_is_no_record(
    forum_config, app_client, session
):
    """
    Projects held a forum token once, and a failure recorded then names it. There
    is no such token to fix any more, so the page shows nothing until the next
    failed publish records a credential that exists.
    """
    project = await _project(session)
    client = build_client_for(project, await local_settings.get(session))
    client.forum.publish_error = {
        "reason": "refused",
        "credential": "project",
        "message": "Permission denied",
        "at": "t",
    }

    resp = await app_client.get(f"/projects/{project.name}/forum/status")

    assert resp.status_code == 200
    assert resp.json()["publish_error"] is None


@pytest.mark.anyio
async def test_forum_status_says_this_project_has_no_lab(
    forum_config, app_client, session
):
    """
    How the page knows to say so instead of offering a debate.

    A project with no repository has nowhere to publish, and `enabled: false` is
    the whole signal — the same shape the deployment-wide switch used to produce,
    now answered per project.
    """
    project = await _project(session, name="no-lab", forum_repo_url=None)
    body = (await app_client.get(f"/projects/{project.name}/forum/status")).json()
    assert body == {
        "enabled": False,
        "shared": False,
        "remote": None,
        "git_ok": True,
        "git_reason": None,
        "unpublished": 0,
        "publish_error": None,
    }


@pytest.mark.anyio
async def test_one_projects_lab_does_not_answer_for_another(
    forum_config, app_client, session
):
    """
    Two projects, two rooms. A forum is a room whose guest list is the push
    access on its repository, so answering with a neighbour's state would tell
    someone their debate is published where it is not.
    """
    with_lab = await _project(session, name="has-lab")
    without = await _project(session, name="has-none", forum_repo_url=None)

    assert (await app_client.get(f"/projects/{with_lab.name}/forum/status")).json()[
        "enabled"
    ]
    assert not (await app_client.get(f"/projects/{without.name}/forum/status")).json()[
        "enabled"
    ]


@pytest.mark.anyio
async def test_forum_status_does_not_call_a_forum_shared_on_the_project_alone(
    forum_config, app_client, session
):
    """
    A URL saved on the project is an intention, not a fact. The forum can still
    be publishing only to its local bare repo — reporting `shared: true` there
    tells the operator outsiders can reach something they cannot. The
    repository's own remote is the one that counts.
    """
    project = await _project(session)
    # Created without `ensure_forum`, so the remote was never applied.
    body = (await app_client.get(f"/projects/{project.name}/forum/status")).json()

    assert body["shared"] is False
    assert body["remote"] is None, "do not advertise a remote the forum has not taken"


@pytest.mark.anyio
async def test_forum_status_is_quiet_when_the_feature_is_off(
    app_client, session, monkeypatch
):
    monkeypatch.setattr(settings, "forum", ForumSettings(enabled=False))
    project = await _project(session)
    body = (await app_client.get(f"/projects/{project.name}/forum/status")).json()
    assert body["enabled"] is False


@pytest.mark.anyio
async def test_forum_status_survives_an_unreadable_forum(
    forum_config, app_client, session, monkeypatch
):
    """An endpoint whose job is reporting bad states must not fail on one."""
    project = await _project(session)

    async def unreadable(self):
        raise RuntimeError("the repository is gone")

    monkeypatch.setattr(forum_config_client(), "remote", unreadable)
    resp = await app_client.get(f"/projects/{project.name}/forum/status")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


def forum_config_client():
    from harness.fake_forum import FakeForumClient

    return FakeForumClient


@pytest.fixture
def git_missing(monkeypatch) -> GitCheck:
    from vista_backend.agents.forum import project_forum
    from vista_backend.api import debate as debate_api

    missing = GitCheck(ok=False, reason="Git is not installed.")
    monkeypatch.setattr(project_forum, "git_status", lambda: missing)
    monkeypatch.setattr(debate_api, "git_status", lambda: missing)
    return missing


@pytest.mark.anyio
async def test_forum_status_names_a_missing_git(
    forum_config, app_client, session, git_missing
):
    """The lab is off without git, and the page is told why in words."""
    project = await _project(session)

    body = (await app_client.get(f"/projects/{project.name}/forum/status")).json()
    assert body["enabled"] is False
    assert body["git_ok"] is False
    assert body["git_reason"] == "Git is not installed."


@pytest.mark.anyio
async def test_opening_a_debate_without_git_says_why(
    forum_config, app_client, session, git_missing
):
    """The lab is off without git, and opening a thread says why in words."""
    project = await _project(session)

    resp = await app_client.post(
        f"/projects/{project.name}/threads", json={"topic": "t"}
    )
    assert resp.status_code == 503
    assert "Git is not installed." in resp.json()["detail"]


@pytest.mark.anyio
async def test_forum_status_counts_posts_waiting_to_publish(
    forum_config, app_client, session
):
    project = await _project(session)
    run, client, participant = await _run(session, project)
    client.go_offline()
    await client.post_as(participant, run.thread_id, "written offline", kind="FINDING")

    body = (await app_client.get(f"/projects/{project.name}/forum/status")).json()
    assert body["unpublished"] == 2, "the framing post and the finding"


# --------------------------------------------------------------------------- #
# A thread that is no longer on the forum
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_deleted_thread_shows_its_stored_posts_and_says_so(
    forum_config, app_client, session
):
    project = await _project(session)
    run, client, _ = await _run(session, project)
    await _refresh(session, project.id, run.thread_id, client)
    run_id, thread_id, project_name = run.id, run.thread_id, project.name
    await debate_service.set_status(session, run_id=run_id, status="converged")
    await session.commit()
    client.delete_thread(thread_id)

    body = (
        await app_client.get(f"/projects/{project_name}/threads/{thread_id}")
    ).json()
    assert body["thread"]["thread_missing"] is True
    assert body["thread"]["run"]["thread_missing"] is True
    assert [p["kind"] for p in body["posts"]] == ["TASK"], "the stored copy is shown"

    post = await app_client.post(
        f"/projects/{project_name}/threads/{thread_id}/posts", json={"body": "hello?"}
    )
    assert post.status_code == 409
    cont = await app_client.post(
        f"/projects/{project_name}/threads/{thread_id}/runs", json={"rounds": 1}
    )
    assert cont.status_code == 409
    close = await app_client.post(f"/projects/{project_name}/threads/{thread_id}/close")
    assert close.status_code == 409


@pytest.mark.anyio
async def test_a_debate_from_the_h5i_era_behaves_as_a_missing_thread(
    forum_config, app_client, session
):
    """
    A run from before shared threads has no thread row; listing the threads
    carries it over, and its thread, which this forum cannot name, reads as
    missing.
    """
    project = await _project(session)
    run = await debate_service.create_debate(
        session,
        project_id=project.id,
        topic="an old one",
        thread_id="7f3a9c",  # an h5i id: not a thread this forum can name
        rounds=2,
    )
    await debate_service.set_status(session, run_id=run.id, status="converged")
    project_name = project.name
    await session.commit()

    listed = (await app_client.get(f"/projects/{project_name}/threads")).json()
    assert [t["thread_id"] for t in listed] == ["7f3a9c"]

    resp = await app_client.get(f"/projects/{project_name}/threads/7f3a9c")
    assert resp.status_code == 200
    assert resp.json()["thread"]["thread_missing"] is True
    assert resp.json()["thread"]["run"]["thread_missing"] is True
    post = await app_client.post(
        f"/projects/{project_name}/threads/7f3a9c/posts", json={"body": "hi"}
    )
    assert post.status_code == 409


# --------------------------------------------------------------------------- #
# Saying that something is happening
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_stream_says_what_the_debate_is_doing(
    forum_config, app_client, session, engine
):
    """
    A turn produces nothing until it finishes, so a thread that has stopped
    growing looks the same whether a role is thinking, waiting on a cluster job,
    or dead. Now that a role waits for simulations, that silence lasts minutes.
    """
    project = await _project(session)
    run, client, _ = await _run(session, project)
    run_id, thread_id, project_name = run.id, run.thread_id, project.name
    await debate_service.set_activity(
        session, run_id=run_id, activity="Proposer is thinking · round 1 of 2"
    )
    await session.commit()

    # Closed on the forum, so the stream replays and ends rather than polling
    # forever. Its own refresh reads the close.
    await client.close_thread(thread_id)

    events = await _collect_stream(app_client, project_name, thread_id)
    activity = [data for name, data in events if name == "activity"]
    assert activity, "the stream never said what the debate was doing"
    assert "Proposer is thinking" in activity[0]


@pytest.mark.anyio
async def test_a_finished_debate_reports_no_activity(forum_config, app_client, session):
    """
    A stale activity is worse than none — it is the frozen screen this was added
    to fix, with a caption claiming otherwise.
    """
    project = await _project(session)
    run, _, _ = await _run(session, project)
    run_id, thread_id, project_name = run.id, run.thread_id, project.name
    await debate_service.set_activity(session, run_id=run_id, activity="thinking")
    await debate_service.set_activity(session, run_id=run_id, activity=None)
    await session.commit()

    body = (
        await app_client.get(f"/projects/{project_name}/threads/{thread_id}")
    ).json()
    assert body["thread"]["run"]["activity"] is None
    assert body["thread"]["run"]["activity_since"] is None


@pytest.mark.anyio
async def test_opening_a_debate_records_each_roles_model(
    forum_config, app_client, session, monkeypatch, inference_key
):
    from vista_backend.api import debate as debate_api

    async def fake_task(run_id):
        return None

    monkeypatch.setattr(debate_api, "run_debate_task", fake_task)

    project = await _project(session)
    resp = await app_client.post(
        f"/projects/{project.name}/threads",
        json={
            "topic": "t",
            "rounds": 1,
            "models": {"referee": "openai:referee-b", "reviewer": "  "},
        },
    )
    assert resp.status_code == 200, resp.text

    rows = await debate_service.list_participants(
        session, run_id=uuid.UUID(resp.json()["run"]["id"])
    )
    assert {r.debate_role: r.model for r in rows} == {
        "proposer": None,
        "reviewer": None,  # blank is no choice
        "referee": "openai:referee-b",
    }


@pytest.mark.anyio
async def test_opening_a_debate_rejects_an_unknown_role(
    forum_config, app_client, session
):
    project = await _project(session)
    resp = await app_client.post(
        f"/projects/{project.name}/threads",
        json={"topic": "t", "models": {"judge": "openai:x"}},
    )
    assert resp.status_code == 422
