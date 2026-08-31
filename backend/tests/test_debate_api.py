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
from vista_backend.services.h5i_forum import ForumClient, ParticipantRole, PostKind


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
    # Three module globals outlive a test and would leak between them.
    #
    # The enrollment map is cached for a minute, so whichever test ran first
    # would decide what the others saw. The refresh throttle is keyed by thread
    # id — and the fake numbers threads from a per-repo sequence, so every test
    # gets the *same* id. Left alone, the second test to use a given id is told
    # its thread was refreshed milliseconds ago and skips the read, which reads
    # as "the peer's post never arrived".
    monkeypatch.setattr(debate_api, "_enrollments", None)
    monkeypatch.setattr(debate_api, "_last_refresh", {})
    monkeypatch.setattr(debate_api, "_refresh_locks", {})
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
    await client.post_as_human(
        thread_id, "the 803 K figure is from a fit", kind=PostKind.FINDING
    )

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
# Continuing
# --------------------------------------------------------------------------- #


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
    h5i moves a closed thread to the attic and it accepts no posts. Continuing
    would attach a roster that could not speak, then fail a round in.
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
    h5i has nowhere to put a name: every operator's post is stamped
    `sender="human"`, which is why a peer's post is byte-identical to ours. But
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
    `sender="human"` exactly like ours, and there is nothing in it we know. The
    most an enrollment can say is which *machine* it came from — so the post row
    stays anonymous and the mapping is offered separately.
    """
    project = await _project(session, alice)
    run, client, _ = await _run(session, alice, project)
    # Posted straight to the forum, bypassing the authenticated endpoint: this is
    # what someone else's host looks like from here.
    await client.post_as_human(run.thread_id, "the fit is not a measurement")

    body = (await app_client.get(f"/projects/{project.name}/debates/{run.id}")).json()

    posted = next((p for p in body["posts"] if "not a measurement" in p["body"]), None)
    assert posted is not None, "the peer's post never reached the projection"
    assert posted["authored_by"] is None


@pytest.mark.anyio
async def test_enrolled_origins_are_offered_for_naming_a_machine(
    forum_config, app_client, session, alice
):
    """
    What an enrollment can honestly say, and where it is put.

    It binds a machine to a forge account, so it is returned as a map from origin
    rather than stamped onto posts — a field called `author` on a post would
    invite reading "jqyin wrote this" out of a record that only supports "this
    came from a machine jqyin enrolled".
    """
    (forum_config.repo_root / ".fake-forum.json").write_text(
        json.dumps(
            {
                "threads": {},
                "boxes": {},
                "participants": {},
                "views": {},
                "seq": 0,
                "enrollments": [
                    {
                        "principal": "github.com/user/19734876",
                        "display_name": "jqyin",
                        "origin": "host-504de42f20b4dd28",
                    }
                ],
            }
        )
    )
    project = await _project(session, alice)
    run, _, _ = await _run(session, alice, project)

    body = (await app_client.get(f"/projects/{project.name}/debates/{run.id}")).json()

    assert body["enrolled_origins"] == {
        "host-504de42f20b4dd28": {
            "principal": "github.com/user/19734876",
            "name": "jqyin",
        }
    }


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
async def test_forum_status_does_not_call_a_forum_shared_on_the_settings_alone(
    forum_config, app_client, monkeypatch
):
    """
    A configured `remote_url` is an intention, not a fact. `ensure_federation`
    logs and carries on when the remote is unreachable at boot, so the setting
    can name a remote the forum never adopted — and reporting `shared: true`
    there tells the operator outsiders can reach a forum that is still purely
    local. h5i's own answer is the one that counts.
    """
    from vista_backend.config import ForumSettings, settings

    monkeypatch.setattr(
        settings,
        "forum",
        ForumSettings(
            enabled=True,
            binary=forum_config.binary,
            repo_root=forum_config.repo_root,
            remote_url="git@github.com:someone/never-applied.git",
        ),
    )

    body = (await app_client.get("/forum/status")).json()

    assert body["shared"] is False
    assert body["remote"] is None, "do not advertise a remote the forum has not taken"


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
