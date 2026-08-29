"""
Tests for the debate service (run / participant / post projection).

The projection is the part worth testing hard. Git owns what was said; these
tables only mirror it, so the properties that matter are that a replay converges
instead of duplicating, that a late vote reaches an old post, and that the
host-stamped and agent-claimed halves stay apart on the way in.
"""

import uuid

import pytest

from vista_backend.db.schemas import ProjectTable
from vista_backend.services import debate as debate_service
from vista_backend.services.h5i_forum import Participant, ParticipantRole, Thread


def _thread(
    *posts: dict, status: str = "open", votes: list[dict] | None = None
) -> Thread:
    """Build a `forum read --json` payload the way h5i emits one."""
    all_posts = [*posts, *(votes or [])]
    return Thread.from_json(
        {
            "header": {
                "id": "t1",
                "title": "why does the knee move?",
                "created_at": "2026-08-27T00:00:00Z",
                "created_by": "human",
            },
            "status": status,
            "posts": all_posts,
            "vouch": [{"id": p["id"], "lane": "host-observed"} for p in all_posts],
        }
    )


def _post(pid: str, kind: str, body: str, sender: str, role: str, **extra) -> dict:
    return {
        "id": pid,
        "thread": "t1",
        "kind": kind,
        "body": body,
        "sender": sender,
        "role": role,
        "ts": f"2026-08-27T00:00:{int(pid[1:]):02d}Z",
        **extra,
    }


async def _make_run(session, alice, **overrides):
    project = ProjectTable(name=f"debate-{overrides.pop('slug', 'p')}")
    session.add(project)
    await session.flush()
    return await debate_service.create_debate(
        session,
        project_id=project.id,
        user_id=alice.id,
        topic="why does the knee move?",
        thread_id="t1",
        framing="Debate it.",
        **overrides,
    )


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_create_debate_starts_in_setting_up(session, alice):
    run = await _make_run(session, alice)
    assert run.status == "setting_up"
    assert run.rounds == 5
    assert run.rounds_done == 0
    assert run.verdict is None


@pytest.mark.anyio
async def test_lookup_by_thread_id(session, alice):
    """The way back from a forum id to the VISTA run that owns it."""
    run = await _make_run(session, alice)
    found = await debate_service.get_debate_by_thread(session, "t1")
    assert found is not None and found.id == run.id
    assert await debate_service.get_debate_by_thread(session, "nope") is None


@pytest.mark.anyio
async def test_project_scoping_is_the_access_boundary(session, alice):
    run = await _make_run(session, alice)
    other = ProjectTable(name="someone-elses")
    session.add(other)
    await session.flush()

    with pytest.raises(ValueError):
        await debate_service.require_debate_in_project(
            session, run_id=run.id, project_id=other.id
        )
    assert await debate_service.require_debate_in_project(
        session, run_id=run.id, project_id=run.project_id
    )


@pytest.mark.anyio
async def test_verdict_converges_the_run(session, alice):
    run = await _make_run(session, alice)
    verdict = {"claim": "network rigidity", "predictions": ["shear independence"]}
    updated = await debate_service.record_verdict(
        session, run_id=run.id, verdict=verdict
    )
    assert updated.status == "converged"
    assert updated.verdict == verdict


@pytest.mark.anyio
async def test_a_debate_closed_early_keeps_a_null_verdict(session, alice):
    """
    A real outcome, not a failure: the human can stop a debate before the referee
    ever rules, and the record should say so rather than inventing a conclusion.
    """
    run = await _make_run(session, alice)
    closed = await debate_service.set_status(session, run_id=run.id, status="closed")
    assert closed.status == "closed"
    assert closed.verdict is None


# --------------------------------------------------------------------------- #
# Participants
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_participant_records_the_box_and_its_policy(session, alice):
    run = await _make_run(session, alice)
    row = await debate_service.add_participant(
        session,
        run_id=run.id,
        debate_role="proposer",
        participant=Participant(
            identity="vista-proposer",
            role=ParticipantRole.WORKER,
            box_slug="proposer",
            box_id="env/human/proposer",
            policy_digest="16f7e744",
        ),
    )
    # VISTA's role vocabulary and h5i's are both kept: they are not the same set.
    assert row.debate_role == "proposer"
    assert row.forum_role == "worker"
    assert row.box_id == "env/human/proposer"
    assert row.policy_digest == "16f7e744"
    assert row.active


@pytest.mark.anyio
async def test_revoking_keeps_the_participant_row(session, alice):
    run = await _make_run(session, alice)
    await debate_service.add_participant(
        session,
        run_id=run.id,
        debate_role="reviewer",
        participant=Participant(
            identity="vista-reviewer",
            role=ParticipantRole.REVIEWER,
            box_slug="reviewer",
            box_id="env/human/reviewer",
        ),
    )
    await debate_service.deactivate_participant(
        session, run_id=run.id, identity="vista-reviewer"
    )
    rows = await debate_service.list_participants(session, run_id=run.id)
    assert len(rows) == 1 and not rows[0].active


# --------------------------------------------------------------------------- #
# Projection
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_projection_keeps_host_stamped_fields_apart_from_the_body(session, alice):
    run = await _make_run(session, alice)
    thread = _thread(
        _post(
            "p1",
            "PROPOSAL",
            "rigidity sets the knee",
            "vista-proposer",
            "worker",
            box_id="env/human/proposer",
            policy_digest="16f7e744",
            origin="host-592619",
        )
    )
    (row,) = await debate_service.project_thread(
        session, run_id=run.id, thread=thread, round_index=0
    )
    assert row.body == "rigidity sets the knee"
    assert row.sender == "vista-proposer"
    assert row.forum_role == "worker"
    assert row.box_id == "env/human/proposer"
    assert row.policy_digest == "16f7e744"
    assert row.vouch_lane == "host-observed"


@pytest.mark.anyio
async def test_replaying_a_thread_converges(session, alice):
    """
    Called on every poll and after every reconnect, so it has to be idempotent —
    otherwise a dropped connection doubles the debate.
    """
    run = await _make_run(session, alice)
    thread = _thread(
        _post("p1", "TASK", "Debate it.", "human", "human"),
        _post("p2", "PROPOSAL", "claim", "vista-proposer", "worker"),
    )
    first = await debate_service.project_thread(session, run_id=run.id, thread=thread)
    second = await debate_service.project_thread(session, run_id=run.id, thread=thread)

    assert len(first) == 2
    assert second == [], "a replay should create nothing"
    assert len(await debate_service.list_posts(session, run_id=run.id)) == 2


@pytest.mark.anyio
async def test_a_late_vote_updates_an_old_post(session, alice):
    """
    Bodies are immutable, tallies are not. A peer can agree with something from
    three rounds ago, and the projection has to carry that back.
    """
    run = await _make_run(session, alice)
    proposal = _post("p1", "PROPOSAL", "claim", "vista-proposer", "worker")

    await debate_service.project_thread(
        session, run_id=run.id, thread=_thread(proposal)
    )
    posts = await debate_service.list_posts(session, run_id=run.id)
    assert posts[0].votes == 0

    voted = _thread(
        proposal,
        votes=[
            _post("p2", "UPVOTE", "+1", "vista-reviewer", "reviewer", reply_to="p1")
        ],
    )
    new = await debate_service.project_thread(session, run_id=run.id, thread=voted)

    posts = await debate_service.list_posts(session, run_id=run.id)
    assert len(posts) == 1, "votes are not turns in the conversation"
    assert posts[0].votes == 1
    assert [p.kind for p in new] == ["UPVOTE"], "the vote itself is still recorded"


@pytest.mark.anyio
async def test_votes_are_stored_but_hidden_from_the_reading_view(session, alice):
    run = await _make_run(session, alice)
    thread = _thread(
        _post("p1", "PROPOSAL", "claim", "vista-proposer", "worker"),
        votes=[
            _post("p2", "UPVOTE", "+1", "vista-reviewer", "reviewer", reply_to="p1")
        ],
    )
    await debate_service.project_thread(session, run_id=run.id, thread=thread)

    assert len(await debate_service.list_posts(session, run_id=run.id)) == 1
    assert (
        len(await debate_service.list_posts(session, run_id=run.id, include_votes=True))
        == 2
    )


@pytest.mark.anyio
async def test_only_agent_posts_are_labelled_with_a_round(session, alice):
    """
    The human's interjections and h5i's own bookkeeping belong to no round, so
    labelling them would put words in a round that did not produce them.
    """
    run = await _make_run(session, alice)
    thread = _thread(
        _post("p1", "TASK", "Debate it.", "human", "human"),
        _post("p2", "PROPOSAL", "claim", "vista-proposer", "worker"),
        _post("p3", "ASK", "constrain to 1 bar", "human", "human"),
        _post("p4", "CLOSED", "closed", "human", "human"),
        status="closed",
    )
    await debate_service.project_thread(
        session, run_id=run.id, thread=thread, round_index=2
    )
    by_id = {
        p.post_id: p for p in await debate_service.list_posts(session, run_id=run.id)
    }
    assert by_id["p2"].round_index == 2, "the agent's proposal is round 2's work"
    assert by_id["p1"].round_index is None, "h5i's TASK belongs to no round"
    assert by_id["p4"].round_index is None, "h5i's CLOSED belongs to no round"
    # A human ASK is a real contribution and still not the agents' round: it
    # happens alongside the debate, not inside it.
    assert by_id["p3"].round_index is None


@pytest.mark.anyio
async def test_a_replay_does_not_relabel_earlier_rounds(session, alice):
    """
    Re-reading a thread mid-round 3 must not stamp round 3 onto round 1's posts.
    """
    run = await _make_run(session, alice)
    round0 = _post("p1", "PROPOSAL", "first", "vista-proposer", "worker")
    await debate_service.project_thread(
        session, run_id=run.id, thread=_thread(round0), round_index=0
    )

    round1 = _post("p2", "RISK", "second", "vista-reviewer", "reviewer")
    await debate_service.project_thread(
        session, run_id=run.id, thread=_thread(round0, round1), round_index=1
    )

    by_id = {
        p.post_id: p for p in await debate_service.list_posts(session, run_id=run.id)
    }
    assert by_id["p1"].round_index == 0
    assert by_id["p2"].round_index == 1


@pytest.mark.anyio
async def test_a_denied_post_is_projected_with_its_refusal(session, alice):
    """
    h5i lets a refused message through and records that it should not have been
    sent. Dropping the refusal on the way into the DB would turn evidence into an
    ordinary contribution.
    """
    run = await _make_run(session, alice)
    thread = _thread(
        _post(
            "p1",
            "FINDING",
            "from a revoked sender",
            "vista-proposer",
            "worker",
            denied="sender revoked at 2026-08-27T18:15:38Z",
        )
    )
    (row,) = await debate_service.project_thread(session, run_id=run.id, thread=thread)
    assert row.denied == "sender revoked at 2026-08-27T18:15:38Z"


@pytest.mark.anyio
async def test_posts_are_ordered_by_forum_timestamp(session, alice):
    run = await _make_run(session, alice)
    thread = _thread(
        _post("p3", "FINDING", "third", "vista-proposer", "worker"),
        _post("p1", "TASK", "first", "human", "human"),
        _post("p2", "PROPOSAL", "second", "vista-proposer", "worker"),
    )
    await debate_service.project_thread(session, run_id=run.id, thread=thread)
    assert [
        p.body for p in await debate_service.list_posts(session, run_id=run.id)
    ] == [
        "first",
        "second",
        "third",
    ]


# --------------------------------------------------------------------------- #
# Refreshing from the forum
#
# Our own agents' posts reach the projection through the debate task. A peer's
# do not: they are published to the remote, and something here has to look.
# --------------------------------------------------------------------------- #


FAKE = __import__("pathlib").Path(__file__).parent / "fixtures" / "fake_h5i.py"


@pytest.fixture
def forum_client(tmp_path):
    from vista_backend.config import ForumSettings
    from vista_backend.services.h5i_forum import ForumClient

    (tmp_path / ".git" / ".h5i").mkdir(parents=True)
    return ForumClient(
        ForumSettings(enabled=True, binary=str(FAKE), repo_root=tmp_path, timeout=30.0),
        confirm_delay=0.0,
    )


async def _live_run(session, alice, client, *, topic="does the knee move?"):
    project = ProjectTable(name=f"refresh-{uuid.uuid4().hex[:8]}")
    session.add(project)
    await session.flush()
    thread_id = await client.create_thread(topic, body="Debate it.")
    return await debate_service.create_debate(
        session,
        project_id=project.id,
        user_id=alice.id,
        topic=topic,
        thread_id=thread_id,
        rounds=2,
    )


@pytest.mark.anyio
async def test_a_refresh_picks_up_a_post_made_outside_the_debate(
    session, alice, forum_client
):
    run = await _live_run(session, alice, forum_client)
    await debate_service.set_status(session, run_id=run.id, status="debating")
    await debate_service.refresh_from_forum(session, forum_client, run)

    # Something arrives on the thread with nothing on our side reading it.
    await forum_client.post_as_human(run.thread_id, "a comment from elsewhere")

    before = await debate_service.list_posts(session, run_id=run.id)
    result = await debate_service.refresh_from_forum(session, forum_client, run)
    after = await debate_service.list_posts(session, run_id=run.id)

    assert len(result.new_posts) == 1
    assert len(after) == len(before) + 1
    assert "a comment from elsewhere" in [p.body for p in after]


@pytest.mark.anyio
async def test_a_refresh_is_idempotent(session, alice, forum_client):
    run = await _live_run(session, alice, forum_client)
    first = await debate_service.refresh_from_forum(session, forum_client, run)
    second = await debate_service.refresh_from_forum(session, forum_client, run)

    assert first.new_posts and second.new_posts == []


@pytest.mark.anyio
async def test_a_refresh_notices_the_thread_was_closed_elsewhere(
    session, alice, forum_client
):
    """
    A peer with push access can close a thread they did not open (contract §8.2),
    so the forum's status can move without anything here deciding it did.
    """
    run = await _live_run(session, alice, forum_client)
    await debate_service.set_status(session, run_id=run.id, status="debating")
    await forum_client.close_thread(run.thread_id)

    result = await debate_service.refresh_from_forum(session, forum_client, run)

    assert result.closed_remotely
    assert (await debate_service.require_debate(session, run.id)).status == "closed"


@pytest.mark.anyio
async def test_a_run_that_already_ended_is_not_reclosed(session, alice, forum_client):
    run = await _live_run(session, alice, forum_client)
    await debate_service.record_verdict(session, run_id=run.id, verdict={"r": 1})
    await forum_client.close_thread(run.thread_id)

    result = await debate_service.refresh_from_forum(session, forum_client, run)

    assert not result.closed_remotely, "converged is terminal; closure does not undo it"
    assert (await debate_service.require_debate(session, run.id)).status == "converged"


# --------------------------------------------------------------------------- #
# Who ended it
# --------------------------------------------------------------------------- #


def _closed_post(lane: str):
    from vista_backend.db.schemas import DebatePostTable

    return DebatePostTable(
        run_id=uuid.uuid4(),
        post_id="c1",
        kind="CLOSED",
        body="closed",
        sender="human",
        forum_role="human",
        ts="2026-08-29T00:00:00Z",
        vouch_lane=lane,
        votes=0,
        tools_used=[],
    )


def test_an_open_debate_has_no_closer():
    assert debate_service.closed_by([]) is None


def test_our_own_closure_is_attributed_to_the_operator():
    assert debate_service.closed_by([_closed_post("host-observed")]) == "operator"


def test_a_peers_closure_is_not_credited_to_the_operator():
    """
    Both arrive with `sender == "human"`, because every host stamps its own
    operator that way. Calling a peer's closure "ended early" would tell the
    reader you made a decision somebody else made.
    """
    assert debate_service.closed_by([_closed_post("peer-claimed")]) == "peer"
    assert debate_service.closed_by([_closed_post("unattributed")]) == "peer"
