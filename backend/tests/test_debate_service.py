"""
Tests for the debate service (run / participant / post projection).

The projection is the part worth testing hard. Git owns what was said; these
tables only mirror it, so the properties that matter are that a replay converges
instead of duplicating, that a late vote reaches an old post, and that the
host-stamped and agent-claimed halves stay apart on the way in.
"""

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
