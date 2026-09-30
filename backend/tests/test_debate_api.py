"""
Tests for the debate API.

Real HTTP through an ASGI transport, a real DB session, and the in-memory fake
forum, so the routes, the access boundary and the event stream are all exercised. The
background argument is not started here — these test the surface a client sees,
and the loop has its own driver tests.
"""

import asyncio
import json
import uuid

import anyio
import httpx
import pytest

from vista_backend.agents.forum.project_forum import build_client_for
from vista_backend.config import ForumSettings, settings
from vista_backend.db.schemas import ProjectCreate
from vista_backend.services import debate as debate_service
from vista_backend.services import project as project_service
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


async def _project(session, user, name="api-debate", forum_repo_url=FORUM_URL):
    """
    A project with a Hypothesis Lab, unless the caller asks for one without.

    Not through `ensure_forum`, which would run git and sync: these tests are
    about the API, and the setup path has its own file.
    """
    return await project_service.create_project(
        session, ProjectCreate(name=name, forum_repo_url=forum_repo_url), user
    )


async def _run(session, user, project, *, topic="why does the knee move?"):
    """A debate row with a real forum thread behind it."""
    client = build_client_for(project)
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
    participant = Participant(
        identity=f"vista-proposer-{str(run.id)[:8]}", role="proposer"
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
async def test_get_picks_up_a_peer_post_on_a_finished_debate(
    forum_config, app_client, session, alice
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
    project = await _project(session, alice)
    run, client, _ = await _run(session, alice, project)
    await debate_service.project_thread(
        session, run_id=run.id, thread=await client.read_thread(run.thread_id)
    )
    # Read before committing: a commit expires every object the session holds,
    # and touching one afterwards is async IO where none can be awaited.
    run_id, thread_id, project_name = run.id, run.thread_id, project.name
    await debate_service.set_status(session, run_id=run_id, status="converged")
    await session.commit()

    # A peer publishes to the remote; nothing local knows yet.
    client.peer_post(thread_id, "the 803 K figure is from a fit", kind="FINDING")

    body = (await app_client.get(f"/projects/{project_name}/debates/{run_id}")).json()

    assert "FINDING" in [p["kind"] for p in body["posts"]], (
        "a peer commented on a finished debate and the interface never showed it"
    )


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
    The UI has to be able to draw the known/claimed boundary, so the API must
    not flatten it into one blob of text.
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
    for field in ("sender", "forum_role", "origin"):
        assert proposal[field], f"{field} must reach the client"
    assert proposal["vouch_lane"] == "host-observed"
    # No remote was set on this forum, so our post is honestly not yet published.
    assert proposal["published"] is False


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
async def test_a_closed_thread_refuses_the_humans_post_too(
    forum_config, app_client, session, alice
):
    """
    Readers ignore everything after the first CLOSED, so a post there would be
    written and never shown. Saying no is the honest answer.
    """
    project = await _project(session, alice)
    run, client, _ = await _run(session, alice, project)
    await client.close_thread(run.thread_id)

    resp = await app_client.post(
        f"/projects/{project.name}/debates/{run.id}/posts",
        json={"body": "for the record: we stopped because of the shear data"},
    )
    assert resp.status_code == 409
    assert "closed" in resp.json()["detail"]


@pytest.mark.anyio
async def test_an_unpostable_kind_is_rejected(forum_config, app_client, session, alice):
    """
    `CLOSED` is a real kind, written only by closing; `CLAIM` is not a kind at
    all. Neither may be posted.
    """
    project = await _project(session, alice)
    run, _, _ = await _run(session, alice, project)

    for kind in ("CLAIM", "CLOSED", "UPVOTE"):
        resp = await app_client.post(
            f"/projects/{project.name}/debates/{run.id}/posts",
            json={"body": "mine now", "kind": kind},
        )
        assert resp.status_code == 422, kind


# --------------------------------------------------------------------------- #
# Closing
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_opening_a_debate_with_no_inference_key_says_where_to_add_one(
    forum_config, app_client, session, alice, monkeypatch
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
    project = await _project(session, alice)

    resp = await app_client.post(
        f"/projects/{project.name}/debates", json={"topic": "why does the knee move?"}
    )

    assert resp.status_code == 409, resp.text
    assert SETTINGS_LOCATION in resp.json()["detail"]
    assert await debate_service.list_debates(session, project_id=project.id) == []


@pytest.mark.anyio
async def test_continuing_a_debate_whose_opener_has_no_key_says_where_to_add_one(
    forum_config, app_client, session, alice, monkeypatch
):
    """
    More rounds run on the opener's settings, so with no key there the request
    is refused up front, rather than accepted and then failing in a background
    task where nobody sees why.
    """
    from vista_backend.agents.inference import SETTINGS_LOCATION

    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "model", "openai:deployment-model")
    project = await _project(session, alice)
    run, _, _ = await _run(session, alice, project)
    await debate_service.set_status(session, run_id=run.id, status="converged")

    resp = await app_client.post(
        f"/projects/{project.name}/debates/{run.id}/continue", json={"rounds": 1}
    )

    assert resp.status_code == 409, resp.text
    assert SETTINGS_LOCATION in resp.json()["detail"]
    assert (await debate_service.require_debate(session, run.id)).status == "converged"


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


@pytest.mark.anyio
async def test_the_stream_resends_a_post_whose_provenance_lands_late(
    forum_config, app_client, session, engine, alice, monkeypatch
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

    project = await _project(session, alice)
    run, client, participant = await _run(session, alice, project)
    post = await client.post_as(participant, run.thread_id, "rigidity", kind="PROPOSAL")
    await debate_service.project_thread(
        session, run_id=run.id, thread=await client.read_thread(run.thread_id)
    )
    run_id, project_name, post_id = run.id, project.name, post.id
    await session.commit()

    monkeypatch.setattr(
        debate_api, "stream_session_factory", lambda: AsyncSession(engine)
    )

    async def record_the_tools_then_finish():
        await asyncio.sleep(0.05)
        async with AsyncSession(engine) as s:
            await debate_service.record_post_tools(
                s,
                run_id=run_id,
                post_id=post_id,
                tools=[{"tool": "search_literature", "detail": "FLiBe viscosity"}],
            )
            await debate_service.set_status(s, run_id=run_id, status="closed")
            await s.commit()

    writer = asyncio.create_task(record_the_tools_then_finish())
    try:
        events = await _collect_stream(app_client, project_name, run_id)
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
    forum_config, app_client, session, engine, alice, monkeypatch
):
    """
    The fingerprint has to be the payload, not the poll.

    Re-emitting on every pass would work and would also push a post a second
    every time anyone watches a live debate — a fix that trades a stale field for
    a flood.
    """
    from sqlmodel.ext.asyncio.session import AsyncSession

    from vista_backend.api import debate as debate_api

    project = await _project(session, alice)
    run, client, _participant = await _run(session, alice, project)
    await debate_service.project_thread(
        session, run_id=run.id, thread=await client.read_thread(run.thread_id)
    )
    run_id, project_name = run.id, project.name
    await session.commit()

    monkeypatch.setattr(
        debate_api, "stream_session_factory", lambda: AsyncSession(engine)
    )

    # Counted, not timed. Sleeping "a few poll intervals" and hoping looks like a
    # test and is not one: with the fixture's 0.01s interval the stream got
    # through a single pass before the run closed, so re-emitting on every poll
    # left this green.
    polls = 0
    real_list_posts = debate_service.list_posts

    async def counting_list_posts(session, *, run_id):
        nonlocal polls
        polls += 1
        return await real_list_posts(session, run_id=run_id)

    monkeypatch.setattr(debate_service, "list_posts", counting_list_posts)

    async def close_once_it_has_polled_enough():
        while polls < 4:
            await asyncio.sleep(debate_api.STREAM_POLL_SECONDS / 2)
        async with AsyncSession(engine) as s:
            await debate_service.set_status(s, run_id=run_id, status="closed")
            await s.commit()

    writer = asyncio.create_task(close_once_it_has_polled_enough())
    try:
        events = await _collect_stream(app_client, project_name, run_id)
    finally:
        await writer

    assert polls >= 4, "the stream has to have looked more than once"
    posts = [d for e, d in events if e == "post"]
    assert len(posts) == 1, "an unchanged post is sent exactly once"


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
    assert all(r.identity.endswith(str(run_id)[:8]) for r in rows)


@pytest.mark.anyio
async def test_opening_a_debate_is_503_when_the_forum_is_off(
    app_client, session, alice, monkeypatch
):
    """A deployment with the forum off should say so, not fail deep in git."""
    monkeypatch.setattr(settings, "forum", ForumSettings(enabled=False))
    project = await _project(session, alice)

    resp = await app_client.post(
        f"/projects/{project.name}/debates", json={"topic": "t"}
    )
    assert resp.status_code == 503


# --------------------------------------------------------------------------- #
# Continuing
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_concluded_debate_a_peer_closed_cannot_be_continued(
    forum_config, app_client, session, alice
):
    """
    Found in the two-install check (task 7.2). A converged run keeps its status
    when a peer closes the thread afterwards, so the CLOSED post is what has to
    refuse more rounds — the status alone would let a roster start that could
    not post.
    """
    project = await _project(session, alice)
    run, client, _ = await _run(session, alice, project)
    run_id, thread_id, project_name = run.id, run.thread_id, project.name
    await debate_service.set_status(session, run_id=run_id, status="converged")
    await session.commit()
    client.peer_close(thread_id)

    body = (await app_client.get(f"/projects/{project_name}/debates/{run_id}")).json()
    assert body["run"]["status"] == "converged", "a verdict is not undone by a close"
    assert body["posts"][-1]["kind"] == "CLOSED"

    resp = await app_client.post(
        f"/projects/{project_name}/debates/{run_id}/continue", json={"rounds": 1}
    )
    assert resp.status_code == 409
    post = await app_client.post(
        f"/projects/{project_name}/debates/{run_id}/posts", json={"body": "hi"}
    )
    assert post.status_code == 409


@pytest.mark.anyio
async def test_continue_raises_the_budget_and_spawns_the_argument(
    forum_config, app_client, session, alice, monkeypatch
):
    from vista_backend.api import debate as debate_api

    spawned: list[tuple[uuid.UUID, int]] = []
    started = asyncio.Event()

    async def fake_task(run_id, extra_rounds):
        spawned.append((run_id, extra_rounds))
        started.set()

    monkeypatch.setattr(debate_api, "continue_debate_task", fake_task)

    project = await _project(session, alice)
    run, _, _ = await _run(session, alice, project)
    run_id, project_name = run.id, project.name
    await debate_service.set_status(session, run_id=run_id, status="converged")
    await session.commit()

    resp = await app_client.post(
        f"/projects/{project_name}/debates/{run_id}/continue", json={"rounds": 3}
    )

    assert resp.status_code == 200
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
    forum_config, app_client, session, alice
):
    """Two orchestrators on one thread would interleave turns and both be wrong."""
    project = await _project(session, alice)
    run, _, _ = await _run(session, alice, project)  # left at "debating"

    resp = await app_client.post(
        f"/projects/{project.name}/debates/{run.id}/continue", json={"rounds": 2}
    )
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_continue_is_refused_on_a_closed_thread(
    forum_config, app_client, session, alice
):
    """
    A closed thread accepts no posts. Continuing would start a roster that
    could not speak, then fail a round in.
    """
    project = await _project(session, alice)
    run, _, _ = await _run(session, alice, project)
    run_id, project_name = run.id, project.name
    await debate_service.set_status(session, run_id=run_id, status="closed")
    await session.commit()

    resp = await app_client.post(
        f"/projects/{project_name}/debates/{run_id}/continue", json={"rounds": 2}
    )
    assert resp.status_code == 409


# --------------------------------------------------------------------------- #
# Who said it
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_human_post_records_the_account_that_wrote_it(
    forum_config, app_client, session, alice
):
    """
    The forum has nowhere to put a name: every operator's post says
    `sender="human"`, which is why a peer's post looks just like ours. But
    the request that made *this* post was authenticated, so who wrote it is
    knowledge here even though the forum cannot carry it.
    """
    project = await _project(session, alice)
    run, _, _ = await _run(session, alice, project)

    resp = await app_client.post(
        f"/projects/{project.name}/debates/{run.id}/posts",
        json={"body": "what about the beryllium supply?", "kind": "ASK"},
    )
    assert resp.status_code == 200
    assert resp.json()["authored_by"] == alice.email


@pytest.mark.anyio
async def test_the_author_survives_the_thread_being_replayed(
    forum_config, app_client, session, alice
):
    """
    The projection re-reads the whole thread on every refresh. If that path
    rebuilt rows wholesale it would erase the one field the forum cannot supply,
    and the name would vanish the first time a peer commented.
    """
    project = await _project(session, alice)
    run, client, _ = await _run(session, alice, project)
    await app_client.post(
        f"/projects/{project.name}/debates/{run.id}/posts",
        json={"body": "and the corrosion data?", "kind": "ASK"},
    )

    await debate_service.project_thread(
        session, run_id=run.id, thread=await client.read_thread(run.thread_id)
    )

    posts = await debate_service.list_posts(session, run_id=run.id)
    asked = next(p for p in posts if p.kind == "ASK")
    assert asked.authored_by == alice.email


@pytest.mark.anyio
async def test_a_peer_post_is_never_given_an_author(
    forum_config, app_client, session, alice
):
    """
    The distinction the whole feature rests on. A peer's post arrives as
    `sender="human"` exactly like ours, and there is nothing in it we know, so
    the post row stays anonymous.
    """
    project = await _project(session, alice)
    run, client, _ = await _run(session, alice, project)
    # Arrived over the remote: this is what someone else's install looks like.
    client.peer_post(
        run.thread_id, "the fit is not a measurement", identity="human", role="human"
    )

    body = (await app_client.get(f"/projects/{project.name}/debates/{run.id}")).json()

    posted = next((p for p in body["posts"] if "not a measurement" in p["body"]), None)
    assert posted is not None, "the peer's post never reached the projection"
    assert posted["authored_by"] is None


# --------------------------------------------------------------------------- #
# Forum status
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_forum_status_reports_a_local_only_forum(
    forum_config, app_client, session, alice
):
    project = await _project(session, alice)
    resp = await app_client.get(f"/projects/{project.name}/forum/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] and not body["shared"]
    assert body["git_ok"] and body["git_reason"] is None
    assert body["unpublished"] == 0


@pytest.mark.anyio
async def test_forum_status_says_this_project_has_no_lab(
    forum_config, app_client, session, alice
):
    """
    How the page knows to say so instead of offering a debate.

    A project with no repository has nowhere to publish, and `enabled: false` is
    the whole signal — the same shape the deployment-wide switch used to produce,
    now answered per project.
    """
    project = await _project(session, alice, name="no-lab", forum_repo_url=None)
    body = (await app_client.get(f"/projects/{project.name}/forum/status")).json()
    assert body == {
        "enabled": False,
        "shared": False,
        "remote": None,
        "git_ok": True,
        "git_reason": None,
        "unpublished": 0,
    }


@pytest.mark.anyio
async def test_one_projects_lab_does_not_answer_for_another(
    forum_config, app_client, session, alice
):
    """
    Two projects, two rooms. A forum is a room whose guest list is the push
    access on its repository, so answering with a neighbour's state would tell
    someone their debate is published where it is not.
    """
    with_lab = await _project(session, alice, name="has-lab")
    without = await _project(session, alice, name="has-none", forum_repo_url=None)

    assert (await app_client.get(f"/projects/{with_lab.name}/forum/status")).json()[
        "enabled"
    ]
    assert not (await app_client.get(f"/projects/{without.name}/forum/status")).json()[
        "enabled"
    ]


@pytest.mark.anyio
async def test_forum_status_does_not_call_a_forum_shared_on_the_project_alone(
    forum_config, app_client, session, alice
):
    """
    A URL saved on the project is an intention, not a fact. The forum can still
    be publishing only to its local bare repo — reporting `shared: true` there
    tells the operator outsiders can reach something they cannot. The
    repository's own remote is the one that counts.
    """
    project = await _project(session, alice)
    # Created without `ensure_forum`, so the remote was never applied.
    body = (await app_client.get(f"/projects/{project.name}/forum/status")).json()

    assert body["shared"] is False
    assert body["remote"] is None, "do not advertise a remote the forum has not taken"


@pytest.mark.anyio
async def test_forum_status_is_quiet_when_the_feature_is_off(
    app_client, session, alice, monkeypatch
):
    from vista_backend.config import ForumSettings, settings

    monkeypatch.setattr(settings, "forum", ForumSettings(enabled=False))
    project = await _project(session, alice)
    body = (await app_client.get(f"/projects/{project.name}/forum/status")).json()
    assert body["enabled"] is False


@pytest.mark.anyio
async def test_forum_status_survives_an_unreadable_forum(
    forum_config, app_client, session, alice, monkeypatch
):
    """An endpoint whose job is reporting bad states must not fail on one."""
    project = await _project(session, alice)

    async def unreadable(self):
        raise RuntimeError("the repository is gone")

    monkeypatch.setattr(forum_config_client(), "remote", unreadable)
    resp = await app_client.get(f"/projects/{project.name}/forum/status")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


def forum_config_client():
    from harness.fake_forum import FakeForumClient

    return FakeForumClient


@pytest.mark.anyio
async def test_forum_status_names_a_missing_git(
    forum_config, app_client, session, alice, monkeypatch
):
    """The lab is off without git, and the page is told why in words."""
    from vista_backend.agents.forum import project_forum
    from vista_backend.api import debate as debate_api

    missing = GitCheck(ok=False, reason="Git is not installed.")
    monkeypatch.setattr(project_forum, "git_status", lambda: missing)
    monkeypatch.setattr(debate_api, "git_status", lambda: missing)
    project = await _project(session, alice)

    body = (await app_client.get(f"/projects/{project.name}/forum/status")).json()
    assert body["enabled"] is False
    assert body["git_ok"] is False
    assert body["git_reason"] == "Git is not installed."

    resp = await app_client.post(
        f"/projects/{project.name}/debates", json={"topic": "t"}
    )
    assert resp.status_code == 503
    assert "Git is not installed." in resp.json()["detail"]


@pytest.mark.anyio
async def test_forum_status_counts_posts_waiting_to_publish(
    forum_config, app_client, session, alice
):
    project = await _project(session, alice)
    run, client, participant = await _run(session, alice, project)
    client.go_offline()
    await client.post_as(participant, run.thread_id, "written offline", kind="FINDING")

    body = (await app_client.get(f"/projects/{project.name}/forum/status")).json()
    assert body["unpublished"] == 2, "the framing post and the finding"


# --------------------------------------------------------------------------- #
# A thread that is no longer on the forum
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_deleted_thread_shows_its_stored_posts_and_says_so(
    forum_config, app_client, session, alice
):
    project = await _project(session, alice)
    run, client, _ = await _run(session, alice, project)
    await debate_service.project_thread(
        session, run_id=run.id, thread=await client.read_thread(run.thread_id)
    )
    run_id, thread_id, project_name = run.id, run.thread_id, project.name
    await debate_service.set_status(session, run_id=run_id, status="converged")
    await session.commit()
    client.delete_thread(thread_id)

    body = (await app_client.get(f"/projects/{project_name}/debates/{run_id}")).json()
    assert body["run"]["thread_missing"] is True
    assert [p["kind"] for p in body["posts"]] == ["TASK"], "the stored copy is shown"

    post = await app_client.post(
        f"/projects/{project_name}/debates/{run_id}/posts", json={"body": "hello?"}
    )
    assert post.status_code == 409
    cont = await app_client.post(
        f"/projects/{project_name}/debates/{run_id}/continue", json={"rounds": 1}
    )
    assert cont.status_code == 409
    close = await app_client.post(f"/projects/{project_name}/debates/{run_id}/close")
    assert close.status_code == 409


@pytest.mark.anyio
async def test_a_debate_from_the_h5i_era_behaves_as_a_missing_thread(
    forum_config, app_client, session, alice
):
    project = await _project(session, alice)
    run = await debate_service.create_debate(
        session,
        project_id=project.id,
        user_id=alice.id,
        topic="an old one",
        thread_id="7f3a9c",  # an h5i id: not a thread this forum can name
        rounds=2,
    )
    await debate_service.set_status(session, run_id=run.id, status="converged")
    run_id, project_name = run.id, project.name
    await session.commit()

    resp = await app_client.get(f"/projects/{project_name}/debates/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["run"]["thread_missing"] is True
    post = await app_client.post(
        f"/projects/{project_name}/debates/{run_id}/posts", json={"body": "hi"}
    )
    assert post.status_code == 409


# --------------------------------------------------------------------------- #
# Saying that something is happening
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_stream_says_what_the_debate_is_doing(
    forum_config, app_client, session, alice, engine
):
    """
    A turn produces nothing until it finishes, so a thread that has stopped
    growing looks the same whether a role is thinking, waiting on a cluster job,
    or dead. Now that a role waits for simulations, that silence lasts minutes.
    """
    project = await _project(session, alice)
    run, _, _ = await _run(session, alice, project)
    run_id, project_name = run.id, project.name
    await debate_service.set_activity(
        session, run_id=run_id, activity="Proposer is thinking · round 1 of 2"
    )
    await session.commit()

    # Terminal status, so the stream replays and ends rather than polling forever.
    await debate_service.set_status(session, run_id=run_id, status="converged")
    await session.commit()

    events = await _collect_stream(app_client, project_name, run_id)
    activity = [data for name, data in events if name == "activity"]
    assert activity, "the stream never said what the debate was doing"
    assert "Proposer is thinking" in activity[0]


@pytest.mark.anyio
async def test_a_finished_debate_reports_no_activity(
    forum_config, app_client, session, alice
):
    """
    A stale activity is worse than none — it is the frozen screen this was added
    to fix, with a caption claiming otherwise.
    """
    project = await _project(session, alice)
    run, _, _ = await _run(session, alice, project)
    run_id, project_name = run.id, project.name
    await debate_service.set_activity(session, run_id=run_id, activity="thinking")
    await debate_service.set_activity(session, run_id=run_id, activity=None)
    await session.commit()

    body = (await app_client.get(f"/projects/{project_name}/debates/{run_id}")).json()
    assert body["run"]["activity"] is None
    assert body["run"]["activity_since"] is None
