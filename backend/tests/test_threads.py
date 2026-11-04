"""
Shared threads: every thread on a project's forum, copied into the tables
(openspec/changes/forum-shared-threads, group 2).

The projection tests use hand-built `Thread`s, like `test_debate_service.py`.
The sync tests run two real forum clients against one bare repository, like
`test_forum_git.py`, so "Bob's install lists Alice's thread" is tested on git.
"""

import shutil
import subprocess
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from vista_backend.config import ForumSettings
from vista_backend.db.schemas import ProjectTable
from vista_backend.services import debate as debate_service
from vista_backend.services import threads
from vista_backend.services.forum_git import (
    ForumClient,
    Participant,
    Post,
    PostKind,
    RunSpec,
    Thread,
    ThreadHeader,
)

THREAD = "01a0d3c1-db12-747a-b485-fbd38b3b9f8b"
ROSTER = {
    "proposer": "vista-proposer-1a2b3c4d",
    "reviewer": "vista-reviewer-1a2b3c4d",
    "referee": "vista-referee-1a2b3c4d",
}


def _post(pid: str, kind: str, sender: str = "human", **extra) -> Post:
    return Post(
        id=pid,
        thread=THREAD,
        kind=kind,
        body=extra.pop("body", f"{kind} body"),
        sender=sender,
        role=extra.pop("role", sender),
        ts=extra.pop("ts", f"2026-11-04T12:00:{len(pid):02d}+00:00"),
        origin=extra.pop("origin", "a" * 32),
        **extra,
    )


def _thread(*posts: Post, lanes: dict[str, str] | None = None) -> Thread:
    lanes = lanes or {}
    return Thread(
        header=ThreadHeader(
            id=THREAD,
            title="Why does coffee cool faster when you blow on it?",
            created_at="2026-11-04T12:00:00+00:00",
            created_by="a" * 32,
        ),
        status="open",
        posts=list(posts),
        vouch={p.id: lanes.get(p.id, "peer-claimed") for p in posts},
    )


async def _project(session) -> ProjectTable:
    project = ProjectTable(name=f"threads-{uuid.uuid4().hex[:6]}")
    session.add(project)
    await session.flush()
    return project


# --------------------------------------------------------------------------- #
# Projection
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_thread_is_copied_with_its_posts_in_order(session):
    project = await _project(session)
    thread = _thread(
        _post("p1", "TASK", body="Debate it."),
        _post("p2", "PROPOSAL", ROSTER["proposer"]),
        _post("p3", "RISK", ROSTER["reviewer"]),
    )
    row = await threads.project_forum_thread(
        session, project_id=project.id, thread=thread, tip="abc"
    )
    assert row.title.startswith("Why does coffee")
    assert row.framing == "Debate it." and row.created_by == "a" * 32
    assert row.tip == "abc" and row.status == "open"
    posts = await threads.list_posts(session, thread=row)
    assert [p.post_id for p in posts] == ["p1", "p2", "p3"]
    assert {p.vouch_lane for p in posts} == {"peer-claimed"}


@pytest.mark.anyio
async def test_projecting_twice_converges(session):
    project = await _project(session)
    thread = _thread(_post("p1", "PROPOSAL", ROSTER["proposer"]))
    await threads.project_forum_thread(session, project_id=project.id, thread=thread)
    row = await threads.project_forum_thread(
        session, project_id=project.id, thread=thread
    )
    assert len(await threads.list_posts(session, thread=row)) == 1
    assert len(await threads.list_threads(session, project_id=project.id)) == 1


@pytest.mark.anyio
async def test_a_vanished_post_is_kept_and_marked(session):
    project = await _project(session)
    first = _post("p1", "PROPOSAL", ROSTER["proposer"])
    await threads.project_forum_thread(
        session, project_id=project.id, thread=_thread(first)
    )
    row = await threads.project_forum_thread(
        session, project_id=project.id, thread=_thread()
    )
    [kept] = await threads.list_posts(session, thread=row)
    assert kept.post_id == "p1" and kept.on_remote is False


@pytest.mark.anyio
async def test_the_newest_verdict_is_current(session):
    project = await _project(session)
    alices = _post("p1", "DONE", ROSTER["referee"], verdict={"rationale": "Alice's"})
    bobs = _post(
        "p22", "DONE", "vista-referee-99999999", verdict={"rationale": "Bob's"}
    )
    row = await threads.project_forum_thread(
        session, project_id=project.id, thread=_thread(alices, bobs)
    )
    assert row.verdict == {"rationale": "Bob's"}
    current = threads.current_verdict(await threads.list_posts(session, thread=row))
    assert current is not None and current.post_id == "p22"


@pytest.mark.anyio
async def test_a_verdict_without_structure_falls_back_to_its_body(session):
    project = await _project(session)
    row = await threads.project_forum_thread(
        session,
        project_id=project.id,
        thread=_thread(_post("p1", "DONE", body="verdict in prose")),
    )
    assert row.verdict is None
    current = threads.current_verdict(await threads.list_posts(session, thread=row))
    assert current is not None and current.body == "verdict in prose"


@pytest.mark.anyio
async def test_a_live_run_is_read_from_the_stored_posts(session):
    project = await _project(session)
    now = datetime.now(UTC)
    marker = _post(
        "p1",
        "RUN",
        ts=now.isoformat(),
        run=RunSpec(rounds=3, roster=ROSTER),
    )
    row = await threads.project_forum_thread(
        session, project_id=project.id, thread=_thread(marker)
    )
    rows = await threads.list_posts(session, thread=row)
    live = threads.live_run_of(rows, expiry=2700.0, now=now + timedelta(minutes=5))
    assert live is not None and live.rounds == 3
    assert (
        threads.live_run_of(rows, expiry=2700.0, now=now + timedelta(hours=1)) is None
    )


@pytest.mark.anyio
async def test_posts_carry_their_runs_notes(session):
    project = await _project(session)
    run = await debate_service.create_debate(
        session, project_id=project.id, topic="t", thread_id=THREAD
    )
    ours = _post("p1", "PROPOSAL", ROSTER["proposer"])
    peer = _post("p22", "RISK", "vista-reviewer-99999999")
    thread = _thread(ours, peer, lanes={"p1": "host-observed"})
    await debate_service.project_thread(
        session, run_id=run.id, thread=thread, round_index=0
    )
    await debate_service.record_post_model(
        session, run_id=run.id, post_id="p1", model="openai:gpt-x"
    )
    row = await threads.project_forum_thread(
        session, project_id=project.id, thread=thread
    )
    await threads.link_run(session, run=run, thread=row)

    shown = {p.post_id: p for p in await threads.posts_with_notes(session, thread=row)}
    assert shown["p1"].model == "openai:gpt-x" and shown["p1"].run_id == run.id
    assert shown["p1"].round_index == 0
    assert shown["p22"].model is None and shown["p22"].run_id is None


# --------------------------------------------------------------------------- #
# Carry-over (design D10)
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_an_old_run_gets_its_thread(session):
    project = await _project(session)
    run = await debate_service.create_debate(
        session, project_id=project.id, topic="old question", thread_id=THREAD
    )
    await threads.carry_over(session, project_id=project.id, host_id="b" * 32)

    row = await threads.require_thread(session, project_id=project.id, thread_id=THREAD)
    assert row.title == "old question" and row.created_by == "b" * 32
    assert (await debate_service.require_debate(session, run.id)).forum_thread_id == (
        row.id
    )
    await threads.carry_over(session, project_id=project.id, host_id="b" * 32)
    assert len(await threads.list_threads(session, project_id=project.id)) == 1


@pytest.mark.anyio
async def test_a_missing_old_thread_keeps_its_posts(session):
    project = await _project(session)
    run = await debate_service.create_debate(
        session, project_id=project.id, topic="gone", thread_id=THREAD
    )
    await debate_service.project_thread(
        session, run_id=run.id, thread=_thread(_post("p1", "PROPOSAL", "x"))
    )
    await debate_service.mark_thread_missing(session, run_id=run.id)

    await threads.carry_over(session, project_id=project.id, host_id="b" * 32)

    row = await threads.require_thread(session, project_id=project.id, thread_id=THREAD)
    assert row.thread_missing
    assert [p.post_id for p in await threads.list_posts(session, thread=row)] == ["p1"]


# --------------------------------------------------------------------------- #
# Syncing a forum on real git
# --------------------------------------------------------------------------- #

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@pytest.fixture
def remote(tmp_path):
    path = tmp_path / "forge" / "forum.git"
    path.parent.mkdir()
    subprocess.run(
        ["git", "init", "-q", "--bare", path.name], cwd=path.parent, check=True
    )
    return path


async def _host(root, remote) -> ForumClient:
    client = ForumClient(
        ForumSettings(enabled=True, repo_root=root / "forum-git" / "p", timeout=30.0)
    )
    await client.ensure_repo()
    await client.set_remote(str(remote))
    return client


@needs_git
@pytest.mark.anyio
async def test_bob_lists_alices_thread(session, tmp_path, remote):
    project = await _project(session)
    alice = await _host(tmp_path / "alice", remote)
    bob = await _host(tmp_path / "bob", remote)

    thread_id = await alice.create_thread("Alice's question", body="Frame it.")
    outcome = await threads.sync_forum(session, project_id=project.id, client=bob)

    assert outcome.reached and outcome.projected == [thread_id]
    [row] = await threads.list_threads(session, project_id=project.id)
    assert row.thread_id == thread_id and row.created_by == alice.host_id
    assert row.framing == "Frame it."

    again = await threads.sync_forum(session, project_id=project.id, client=bob)
    assert again.projected == []  # tip unchanged: nothing read

    await alice.post_as(
        Participant(identity=ROSTER["proposer"], role="proposer"),
        thread_id,
        "a proposal",
        kind=PostKind.PROPOSAL,
    )
    moved = await threads.sync_forum(session, project_id=project.id, client=bob)
    assert moved.projected == [thread_id]
    kinds = [p.kind for p in await threads.list_posts(session, thread=row)]
    assert kinds == ["TASK", "PROPOSAL"]


@needs_git
@pytest.mark.anyio
async def test_a_thread_deleted_on_the_forge_is_marked_missing(
    session, tmp_path, remote
):
    project = await _project(session)
    alice = await _host(tmp_path / "alice", remote)
    bob = await _host(tmp_path / "bob", remote)
    thread_id = await alice.create_thread("t", body="x")
    await threads.sync_forum(session, project_id=project.id, client=bob)

    subprocess.run(
        ["git", "update-ref", "-d", f"refs/heads/vista-forum/threads/{thread_id}"],
        cwd=remote,
        check=True,
    )
    outcome = await threads.sync_forum(session, project_id=project.id, client=bob)

    assert outcome.missing == [thread_id]
    [row] = await threads.list_threads(session, project_id=project.id)
    assert row.thread_missing


@needs_git
@pytest.mark.anyio
async def test_an_unreachable_forum_keeps_the_list(session, tmp_path, remote):
    project = await _project(session)
    alice = await _host(tmp_path / "alice", remote)
    bob = await _host(tmp_path / "bob", remote)
    await alice.create_thread("t", body="x")
    await threads.sync_forum(session, project_id=project.id, client=bob)

    await bob.set_remote("http://127.0.0.1:9/forum.git")
    outcome = await threads.sync_forum(session, project_id=project.id, client=bob)

    assert not outcome.reached and outcome.missing == []
    [row] = await threads.list_threads(session, project_id=project.id)
    assert not row.thread_missing


@needs_git
@pytest.mark.anyio
async def test_two_installs_share_a_thread_end_to_end(session, tmp_path, remote):
    """
    The change's whole story on real git (task 4.2). Two installs are two
    forum clients with their own host ids and outboxes, and two projects in
    one database, so their rows stay apart as two databases would keep them.

    Alice opens and concludes a debate. Bob's lab lists it; Bob posts new
    evidence, continues it with his own agents, and his Referee's verdict
    becomes the thread's current one in both labs.
    """
    from test_debate_driver import _committing, _roles

    from vista_backend.agents.forum.debate import DebateOrchestrator, identity_for

    alice_project = (await _project(session)).id
    bob_project = (await _project(session)).id
    alice = await _host(tmp_path / "alice", remote)
    bob = await _host(tmp_path / "bob", remote)

    # Alice opens and concludes.
    alice_orch = DebateOrchestrator(
        client=alice, roles=_roles(), checkpoint=_committing
    )
    run = await alice_orch.start(
        session, project_id=alice_project, topic="Why does the knee move?", rounds=1
    )
    thread_id, alice_run_id = run.thread_id, run.id
    await alice_orch.run(session, run)

    # Bob's lab lists it, opened by another install.
    await threads.sync_forum(session, project_id=bob_project, client=bob)
    row = await threads.require_thread(
        session, project_id=bob_project, thread_id=thread_id
    )
    assert row.created_by == alice.host_id != bob.host_id
    assert row.status == "done" and row.verdict is not None
    first = threads.current_verdict(await threads.list_posts(session, thread=row))
    assert first is not None and first.sender == identity_for("referee", alice_run_id)

    # Bob posts, then continues with his own agents.
    await bob.post_as_human(thread_id, "New evidence: the knee moves with pressure.")
    bob_orch = DebateOrchestrator(client=bob, roles=_roles(), checkpoint=_committing)
    bob_run = await bob_orch.adopt(session, thread=row)
    bob_run_id = bob_run.id
    await bob_orch.resume(session, run=bob_run, extra_rounds=1)

    # Both labs read the same thread, ending in Bob's verdict.
    await threads.sync_forum(session, project_id=alice_project, client=alice)
    for project_id in (alice_project, bob_project):
        shared = await threads.require_thread(
            session, project_id=project_id, thread_id=thread_id
        )
        posts = await threads.list_posts(session, thread=shared)
        current = threads.current_verdict(posts)
        assert current is not None
        assert current.sender == identity_for("referee", bob_run_id)
        # The scripted Referees write the same words, so it is the post that
        # tells whose verdict is current.
        assert current.post_id != first.post_id
        assert shared.verdict == current.verdict
        assert [p.kind for p in posts].count("RUN") == 2
        assert threads.live_run_of(posts, expiry=2700.0) is None

    # On Alice's side, Bob's posts are a peer's; Bob's own are his.
    alice_side = await threads.require_thread(
        session, project_id=alice_project, thread_id=thread_id
    )
    lanes = {
        p.sender: p.vouch_lane
        for p in await threads.list_posts(session, thread=alice_side)
        if p.sender.startswith("vista-")
    }
    assert lanes[identity_for("referee", bob_run_id)] == "peer-claimed"
    assert lanes[identity_for("referee", alice_run_id)] == "host-observed"
