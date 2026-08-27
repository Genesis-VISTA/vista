"""
Drive the debate loop end to end, with fakes only at the two real boundaries.

The forum is the `fake_h5i.py` binary, so the actual `ForumClient` and the actual
projection run; the models are `FunctionModel`s returning scripted outputs. What
is under test is therefore the whole path — loop, client, service, tables — with
no LLM, no sandbox and no network.

Follows the shape of `test_campaign_driver.py`: script the model, drive the real
runtime, assert the state transitions.
"""

import uuid
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.models.function import AgentInfo, FunctionModel

from vista_backend.agents.forum.debate import DebateOrchestrator
from vista_backend.agents.forum.roles import RoleAgents
from vista_backend.config import ForumSettings
from vista_backend.db.schemas import ProjectTable
from vista_backend.services import debate as debate_service
from vista_backend.services.h5i_forum import ForumClient, PostKind


FAKE = Path(__file__).parent / "fixtures" / "fake_h5i.py"


HYPOTHESIS = {
    "claim": "Be-F network rigidity sets the 800K knee",
    "mechanism": "intermediate-range order stiffens above percolation",
    "predictions": ["no shear-rate dependence below 1/s"],
    "confidence": 0.6,
    "open_risks": ["no data below 700K"],
}
REFUTE = {
    "stance": "refute",
    "kind": "RISK",
    "objection": "the mechanism is three orders of magnitude too small",
}
CONCEDE = {"stance": "concede"}
VERDICT = {
    "ranked": [{"hypothesis": HYPOTHESIS, "standing": "survived on magnitude"}],
    "rationale": "the alternative was refuted",
    "unresolved": ["measure at 650K"],
}


def scripted(*payloads: dict, capture: list[str] | None = None) -> FunctionModel:
    """
    Return each payload in turn, repeating the last once they run out.

    Repeating rather than raising lets a test set a budget of five rounds without
    scripting five identical proposals.
    """
    calls = {"n": 0}

    def respond(messages, info: AgentInfo) -> ModelResponse:
        if capture is not None:
            capture.append(
                "\n".join(
                    part.content
                    for message in messages
                    for part in message.parts
                    if isinstance(part, UserPromptPart)
                    and isinstance(part.content, str)
                )
            )
        payload = payloads[min(calls["n"], len(payloads) - 1)]
        calls["n"] += 1
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)])

    return FunctionModel(respond)


@pytest.fixture
def client(tmp_path) -> ForumClient:
    (tmp_path / ".git" / ".h5i").mkdir(parents=True)
    return ForumClient(
        ForumSettings(enabled=True, binary=str(FAKE), repo_root=tmp_path, timeout=30.0),
        confirm_delay=0.0,
    )


async def _project(session):
    project = ProjectTable(name=f"debate-driver-{uuid.uuid4().hex[:8]}")
    session.add(project)
    await session.flush()
    return project


async def _start(client, session, alice, *, roles: RoleAgents, rounds=2, on_post=None):
    project = await _project(session)
    orch = DebateOrchestrator(client=client, roles=roles, on_post=on_post)
    run = await orch.start(
        session,
        project_id=project.id,
        user_id=alice.id,
        topic="why does the knee move?",
        framing="1 bar only.",
        rounds=rounds,
    )
    return orch, run


def _roles(*, proposer=None, reviewer=None, referee=None, capture=None) -> RoleAgents:
    """The three roles, each built on its own scripted model."""
    return RoleAgents(
        models={
            "proposer": proposer or scripted(HYPOTHESIS, capture=capture),
            "reviewer": reviewer or scripted(REFUTE),
            "referee": referee or scripted(VERDICT),
        }
    )


# --------------------------------------------------------------------------- #
# The happy path
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_debate_runs_its_rounds_and_ends_with_a_verdict(client, session, alice):
    orch, run = await _start(client, session, alice, roles=_roles(), rounds=2)
    run = await orch.run(session, run)

    assert run.status == "converged"
    assert run.rounds_done == 2
    assert run.verdict is not None
    assert run.verdict["unresolved"] == ["measure at 650K"]

    posts = await debate_service.list_posts(session, run_id=run.id)
    kinds = [p.kind for p in posts]
    assert kinds.count(PostKind.PROPOSAL) == 2
    assert kinds.count(PostKind.RISK) == 2
    assert kinds.count(PostKind.DONE) == 1, "the referee rules exactly once"
    assert kinds[0] == PostKind.TASK, "the human's framing opens the thread"


@pytest.mark.anyio
async def test_each_role_posts_under_its_own_identity(client, session, alice):
    """The whole reason for the box relay, asserted through the real client."""
    orch, run = await _start(client, session, alice, roles=_roles(), rounds=1)
    run = await orch.run(session, run)

    posts = await debate_service.list_posts(session, run_id=run.id)
    by_kind = {p.kind: p for p in posts}
    assert by_kind[PostKind.PROPOSAL].sender.startswith("vista-proposer-")
    assert by_kind[PostKind.RISK].sender.startswith("vista-reviewer-")
    assert by_kind[PostKind.DONE].sender.startswith("vista-referee-")
    assert by_kind[PostKind.PROPOSAL].forum_role == "worker"
    assert by_kind[PostKind.RISK].forum_role == "reviewer"


@pytest.mark.anyio
async def test_the_rebuttal_replies_to_the_proposal_it_attacks(client, session, alice):
    orch, run = await _start(client, session, alice, roles=_roles(), rounds=1)
    run = await orch.run(session, run)

    posts = {p.kind: p for p in await debate_service.list_posts(session, run_id=run.id)}
    assert posts[PostKind.RISK].reply_to == posts[PostKind.PROPOSAL].post_id


# --------------------------------------------------------------------------- #
# Conceding
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_conceding_votes_instead_of_posting(client, session, alice):
    """
    A concession must not become a post. "I agree" as a post costs every later
    reader a turn, and it is the shape a reviewer falls into when it cannot
    concede cheaply.
    """
    roles = _roles(reviewer=scripted(CONCEDE))
    orch, run = await _start(client, session, alice, roles=roles, rounds=1)
    run = await orch.run(session, run)

    posts = await debate_service.list_posts(session, run_id=run.id)
    assert PostKind.RISK not in [p.kind for p in posts]
    assert PostKind.FINDING not in [p.kind for p in posts]

    proposal = next(p for p in posts if p.kind == PostKind.PROPOSAL)
    assert proposal.votes == 1, "the concession lands as an upvote on the proposal"


# --------------------------------------------------------------------------- #
# The human
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_human_post_reaches_the_next_round(client, session, alice):
    """
    There is no separate inbox: the thread is re-read before every turn, so an
    interjection is simply in front of the role when it next speaks.
    """
    prompts: list[str] = []
    roles = _roles(capture=prompts)
    orch, run = await _start(client, session, alice, roles=roles, rounds=2)

    # Round one, then the human interrupts, then round two.
    roster = await orch._participants(session, run.id)  # noqa: SLF001
    run = await orch.run_round(session, run, roster)
    await client.post_as_human(run.thread_id, "ignore pressure effects entirely")
    run = await orch.run_round(session, run, roster)

    assert "ignore pressure effects entirely" not in prompts[0], "not there yet"
    assert any("ignore pressure effects entirely" in p for p in prompts[1:]), (
        "the human's interjection has to reach the agents"
    )


@pytest.mark.anyio
async def test_the_human_can_end_a_debate_early(client, session, alice):
    """
    Closing is enforced by h5i, not by the loop: the next post is simply refused.
    The debate ends without a verdict, which is the honest outcome — inventing
    one would report a conclusion the debate never reached.
    """
    closed = {"done": False}

    async def close_after_first_post(post):
        if not closed["done"]:
            closed["done"] = True
            await client.close_thread(post.thread)

    orch, run = await _start(
        client, session, alice, roles=_roles(), rounds=5, on_post=close_after_first_post
    )
    run = await orch.run(session, run)

    assert run.status == "closed"
    assert run.verdict is None, "a debate stopped early has no verdict"
    assert run.rounds_done < 5


@pytest.mark.anyio
async def test_closing_still_records_what_was_said(client, session, alice):
    """The record should hold the debate up to the stop, plus h5i's CLOSED marker."""
    closed = {"done": False}

    async def close_after_first_post(post):
        if not closed["done"]:
            closed["done"] = True
            await client.close_thread(post.thread)

    orch, run = await _start(
        client, session, alice, roles=_roles(), rounds=5, on_post=close_after_first_post
    )
    run = await orch.run(session, run)

    kinds = [p.kind for p in await debate_service.list_posts(session, run_id=run.id)]
    assert PostKind.PROPOSAL in kinds, "what was said before the stop is kept"
    assert PostKind.CLOSED in kinds, "and so is the fact that it was stopped"
    assert PostKind.DONE not in kinds


# --------------------------------------------------------------------------- #
# Setup and teardown
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_the_roster_is_attached_and_recorded(client, session, alice):
    _, run = await _start(client, session, alice, roles=_roles(), rounds=1)

    rows = await debate_service.list_participants(session, run_id=run.id)
    assert {r.debate_role for r in rows} == {"proposer", "reviewer", "referee"}
    assert all(r.box_id.startswith("env/") for r in rows)
    assert all(r.policy_digest for r in rows), "the confinement is recorded per role"
    assert run.status == "debating"


@pytest.mark.anyio
async def test_the_roster_is_retired_when_the_debate_ends(client, session, alice):
    orch, run = await _start(client, session, alice, roles=_roles(), rounds=1)
    run = await orch.run(session, run)

    rows = await debate_service.list_participants(session, run_id=run.id)
    assert not any(r.active for r in rows), "boxes are not left on the forum"
    # Posts survive their author's revocation: the record is not retracted.
    posts = await debate_service.list_posts(session, run_id=run.id)
    assert any(p.sender.startswith("vista-proposer-") for p in posts)


@pytest.mark.anyio
async def test_participants_are_named_after_their_run(client, session, alice):
    """Several debates share one forum, so slugs cannot be bare role names."""
    _, first = await _start(client, session, alice, roles=_roles(), rounds=1)
    _, second = await _start(client, session, alice, roles=_roles(), rounds=1)

    a = {
        r.identity
        for r in await debate_service.list_participants(session, run_id=first.id)
    }
    b = {
        r.identity
        for r in await debate_service.list_participants(session, run_id=second.id)
    }
    assert not (a & b), "two debates must not collide on identities"


@pytest.mark.anyio
async def test_the_projection_matches_the_forum(client, session, alice):
    """The tables are a mirror; a divergence means the mirror is lying."""
    orch, run = await _start(client, session, alice, roles=_roles(), rounds=2)
    run = await orch.run(session, run)

    thread = await client.read_thread(run.thread_id)
    projected = await debate_service.list_posts(
        session, run_id=run.id, include_votes=True
    )
    assert {p.id for p in thread.posts} == {p.post_id for p in projected}


# --------------------------------------------------------------------------- #
# Checkpointing
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_progress_is_checkpointed_every_round(client, session, alice):
    """
    A debate runs for minutes in one background session. Everything that reads it
    meanwhile — the event stream, a GET, another worker — reads through a
    different session, and an uncommitted transaction is invisible to all of
    them, so the live view would only go live once the debate was over.

    The contract is asserted here rather than by opening a second session and
    looking: the test database is an in-memory SQLite on a `StaticPool`, so every
    session shares one connection and one transaction. A second session there
    sees uncommitted rows regardless, which would make such a test pass whether
    or not the checkpoint existed.
    """
    checkpoints: list[int] = []

    async def spy(_session):
        checkpoints.append(len(checkpoints))

    project = await _project(session)
    orch = DebateOrchestrator(client=client, roles=_roles(), checkpoint=spy)
    run = await orch.start(
        session,
        project_id=project.id,
        user_id=alice.id,
        topic="why does the knee move?",
        rounds=3,
    )
    await orch.run(session, run)

    # Three rounds plus the verdict: progress is durable before the next round
    # starts, not only when the whole argument is over.
    assert len(checkpoints) == 4


@pytest.mark.anyio
async def test_a_failing_checkpoint_does_not_end_the_debate(client, session, alice):
    """Losing durability is bad; losing the argument as well would be worse."""
    calls = {"n": 0}

    async def flaky(_session):
        calls["n"] += 1
        raise RuntimeError("disk full")

    project = await _project(session)
    orch = DebateOrchestrator(client=client, roles=_roles(), checkpoint=flaky)
    run = await orch.start(
        session,
        project_id=project.id,
        user_id=alice.id,
        topic="t",
        rounds=2,
    )
    run = await orch.run(session, run)

    assert calls["n"] > 0
    assert run.status == "converged", "the debate still reached a verdict"


@pytest.mark.anyio
async def test_the_orchestrator_survives_its_own_checkpoints(client, session, alice):
    """
    A commit expires every ORM object the session holds, so a loop that carried
    the run row across one would read `rounds_done` off an expired object — sync
    IO in an async session, which fails as MissingGreenlet. The loop carries an
    id and re-reads instead; this is what proves it.
    """

    async def real_commit(s):
        await s.commit()

    project = await _project(session)
    orch = DebateOrchestrator(client=client, roles=_roles(), checkpoint=real_commit)
    run = await orch.start(
        session,
        project_id=project.id,
        user_id=alice.id,
        topic="t",
        rounds=2,
    )
    run = await orch.run(session, run)

    assert run.rounds_done == 2
    assert run.status == "converged"


@pytest.mark.anyio
async def test_run_round_returns_a_run_the_caller_can_still_use(client, session, alice):
    """
    A round ends by checkpointing, and a checkpoint commits — which expires every
    object the session holds, including the row the round was about. So the round
    has to hand back a re-read row, not the one it just invalidated.

    `run()` happens to re-read at the top of its loop, so it would survive either
    way; this pins the contract for every other caller, which is where the
    original MissingGreenlet came from.
    """

    async def real_commit(s):
        await s.commit()

    project = await _project(session)
    orch = DebateOrchestrator(client=client, roles=_roles(), checkpoint=real_commit)
    run = await orch.start(
        session,
        project_id=project.id,
        user_id=alice.id,
        topic="t",
        rounds=2,
    )
    roster = await orch._participants(session, run.id)  # noqa: SLF001

    run = await orch.run_round(session, run, roster)
    # Reading the returned row must not need a refresh the caller cannot await.
    assert run.rounds_done == 1
    run = await orch.run_round(session, run, roster)
    assert run.rounds_done == 2


# --------------------------------------------------------------------------- #
# Running out of budget
# --------------------------------------------------------------------------- #


def _exhausted() -> FunctionModel:
    """
    A model whose run has run out of requests.

    Raised directly rather than simulated with a tool loop: a fake that keeps
    making the same tool call trips pydantic-ai's per-tool retry guard first, so
    the test would exercise a different failure than the one it is about.
    """

    def respond(messages, info: AgentInfo) -> ModelResponse:
        raise UsageLimitExceeded(
            "The next request would exceed the request_limit of 12"
        )

    return FunctionModel(respond)


def _looping_roles(**overrides) -> RoleAgents:
    models = {
        "proposer": scripted(HYPOTHESIS),
        "reviewer": scripted(REFUTE),
        "referee": scripted(VERDICT),
    }
    models.update(overrides)
    return RoleAgents(models=models)


def test_a_role_turn_carries_an_explicit_request_ceiling():
    """
    Inheriting pydantic-ai's default of 50 is how one stuck role spends a whole
    debate's budget before anyone notices. The ceiling is ours to choose.
    """
    from vista_backend.config import settings

    roles = RoleAgents()
    assert roles.limits.request_limit == settings.forum.max_requests_per_turn
    assert roles.limits.request_limit < 50


@pytest.mark.anyio
async def test_a_role_that_burns_its_budget_does_not_kill_the_debate(
    client, session, alice
):
    """
    A role stuck in a tool loop used to take the whole debate down with an
    unhandled UsageLimitExceeded. The round is lost; the debate is not.
    """
    roles = _looping_roles(proposer=_exhausted())
    orch, run = await _start(client, session, alice, roles=roles, rounds=2)
    run = await orch.run(session, run)

    assert run.status == "converged", "the referee still ruled"
    kinds = [p.kind for p in await debate_service.list_posts(session, run_id=run.id)]
    assert PostKind.BLOCKED in kinds, "the thread says why the round produced nothing"
    assert PostKind.DONE in kinds


@pytest.mark.anyio
async def test_the_blocked_note_says_it_is_not_a_conclusion(client, session, alice):
    """
    Silence from a role reads as agreement. It has to be labelled as a budget
    limit, or the Referee weighs an absence as if it were a concession.
    """
    roles = _looping_roles(reviewer=_exhausted())
    orch, run = await _start(client, session, alice, roles=roles, rounds=1)
    run = await orch.run(session, run)

    posts = await debate_service.list_posts(session, run_id=run.id)
    blocked = next(p for p in posts if p.kind == PostKind.BLOCKED)
    assert "not a conclusion" in blocked.body
    assert blocked.sender.startswith("vista-reviewer-")


@pytest.mark.anyio
async def test_a_referee_that_runs_out_leaves_no_verdict(client, session, alice):
    """A fabricated verdict would be worse than none; the run is marked failed."""
    roles = _looping_roles(referee=_exhausted())
    orch, run = await _start(client, session, alice, roles=roles, rounds=1)
    run = await orch.run(session, run)

    assert run.status == "failed"
    assert run.verdict is None
