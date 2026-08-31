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

from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.agents.forum.debate import ROSTER, DebateOrchestrator
from vista_backend.agents.forum.roles import RoleAgents
from vista_backend.config import ForumSettings
from vista_backend.db.schemas import ProjectTable
from vista_backend.services import debate as debate_service
from vista_backend.services.h5i_forum import (
    ForumClient,
    ParticipantRole,
    PostKind,
)


FAKE = Path(__file__).parent / "fixtures" / "fake_h5i.py"


HYPOTHESIS = {
    "note": (
        "Rigidity, not composition — the Be-F network stiffens above "
        "percolation and that puts the knee at 800K. Testable: no shear-rate "
        "dependence below 1/s. Nothing below 700K to check it against, though."
    ),
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


async def _committing(session: AsyncSession) -> None:
    await session.commit()


async def _start(
    client,
    session,
    alice,
    *,
    roles: RoleAgents,
    rounds=2,
    on_post=None,
    checkpoint=_committing,
):
    """
    A debate started the way the live task starts one.

    The checkpoint commits by default, deliberately. A commit expires every ORM
    object the session holds, and code that carries a row across one fails with
    MissingGreenlet — a trap this module has fallen into more than once, always
    in production, because a no-op checkpoint hides it completely.
    """
    project = await _project(session)
    orch = DebateOrchestrator(
        client=client, roles=roles, on_post=on_post, checkpoint=checkpoint
    )
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

    # At least one per round plus the verdict. Deliberately not an exact count:
    # there is now also a checkpoint before each role speaks, so that no write
    # transaction is held across a turn that may be waiting on a cluster job.
    # Pinning the exact number would make that a breaking change every time the
    # loop gains a durability point, which is the opposite of the intent here.
    assert len(checkpoints) >= 4


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


# --------------------------------------------------------------------------- #
# Provenance reaching the record
# --------------------------------------------------------------------------- #


def _tool_using_proposer() -> FunctionModel:
    """Consults a skill once, then answers."""
    state = {"done": False}

    def respond(messages, info: AgentInfo) -> ModelResponse:
        if not state["done"]:
            state["done"] = True
            return ModelResponse(
                parts=[
                    ToolCallPart("read_domain_guidance", {"skill": "salt-chemistry"})
                ]
            )
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, HYPOTHESIS)]
        )

    return FunctionModel(respond)


@pytest.mark.anyio
async def test_a_posts_provenance_reaches_the_record(client, session, alice):
    """
    The whole point: a reader can tell which claims were grounded. The forum
    carries what was said; how the agent got there is recorded on our side.
    """
    from vista_backend.agents.forum.grounding import Grounding, build_toolsets

    async def skill(name):
        return "salt chemistry says the knee is structural"

    roles = RoleAgents(
        models={
            "proposer": _tool_using_proposer(),
            "reviewer": scripted(REFUTE),
            "referee": scripted(VERDICT),
        },
        toolsets=build_toolsets(Grounding(skills=skill)),
    )
    orch, run = await _start(client, session, alice, roles=roles, rounds=1)
    run = await orch.run(session, run)

    posts = {p.kind: p for p in await debate_service.list_posts(session, run_id=run.id)}
    proposal = posts[PostKind.PROPOSAL]
    assert proposal.tools_used == [
        {
            "tool": "read_domain_guidance",
            "detail": "salt-chemistry",
            # Not just that a skill was read — what it said. A label alone is a
            # claim about grounding; this is the evidence under it, and it was
            # being written to an h5i attachment nothing could open.
            "receipt": (
                "skill salt-chemistry\n\nsalt chemistry says the knee is structural"
            ),
        }
    ]

    # The Reviewer answered from the thread alone, and the record says so rather
    # than leaving it ambiguous.
    assert posts[PostKind.RISK].tools_used == []


@pytest.mark.anyio
async def test_each_role_records_what_it_was_allowed_to_use(client, session, alice):
    from vista_backend.agents.forum.grounding import Grounding, build_toolsets

    async def skill(name):
        return "body"

    roles = RoleAgents(
        models={
            "proposer": scripted(HYPOTHESIS),
            "reviewer": scripted(REFUTE),
            "referee": scripted(VERDICT),
        },
        toolsets=build_toolsets(Grounding(skills=skill)),
    )
    _, run = await _start(client, session, alice, roles=roles, rounds=1)

    by_role = {
        r.debate_role: r.granted_tools
        for r in await debate_service.list_participants(session, run_id=run.id)
    }
    assert by_role["proposer"] == ["read_domain_guidance"]
    assert by_role["reviewer"] == []
    assert by_role["referee"] == []


# --------------------------------------------------------------------------- #
# Continuing a finished debate
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_resume_attaches_a_fresh_roster_and_argues_again(client, session, alice):
    """
    Continuing is not just running the loop again.

    The roster is revoked when a debate concludes, and a revoked identity cannot
    post. So `resume` attaches a new one — under distinct identities, so the
    thread shows plainly that the argument was picked up rather than pretending
    it never stopped, and so the earlier posts keep the names they were made
    under.
    """
    orch, run = await _start(client, session, alice, roles=_roles(), rounds=1)
    run_id = run.id
    await orch.run(session, run)

    before = await debate_service.list_participants(session, run_id=run_id)
    assert before, "the first stint should be on the record"
    assert not any(p.active for p in before), "and retired when it concluded"

    run = await debate_service.require_debate(session, run_id)
    await orch.resume(session, run=run, extra_rounds=2)

    after = await debate_service.list_participants(session, run_id=run_id)
    assert len(after) == len(before) + 3, "a second roster, not a reused one"

    first = {p.identity for p in before}
    second = {p.identity for p in after} - first
    assert len(second) == 3
    assert not (first & second), "a revoked identity must not be posted under again"

    refreshed = await debate_service.require_debate(session, run_id)
    assert refreshed.rounds == 3, "the budget is raised, not reset"
    assert refreshed.rounds_done == 3, "and the extra rounds actually ran"


@pytest.mark.anyio
async def test_the_resumed_rounds_post_under_the_new_roster(client, session, alice):
    """
    The roster the orchestrator rebuilds has to be the attached one.

    `list_participants` returns every stint a debate has had, retired ones
    included, keyed by the same three roles — so building the client's view from
    it would collapse them and hand the loop whichever row happened to sort last
    by identity. Half the time that is a revoked identity whose box is gone.

    Checked after the fact by who the extra rounds were posted under, because
    the roster is only live *while* the argument runs: `run` retires it on the
    way out.
    """
    orch, run = await _start(client, session, alice, roles=_roles(), rounds=1)
    run_id = run.id
    await orch.run(session, run)

    first = {
        p.identity
        for p in await debate_service.list_participants(session, run_id=run_id)
    }
    before = {
        p.post_id for p in await debate_service.list_posts(session, run_id=run_id)
    }

    run = await debate_service.require_debate(session, run_id)
    await orch.resume(session, run=run, extra_rounds=1)

    second = {
        p.identity
        for p in await debate_service.list_participants(session, run_id=run_id)
    } - first
    fresh = [
        p
        for p in await debate_service.list_posts(session, run_id=run_id)
        if p.post_id not in before and p.sender != "human"
    ]

    assert fresh, "continuing should have produced posts"
    assert {p.sender for p in fresh} <= second, (
        "the extra rounds were posted under an identity from the retired stint"
    )


@pytest.mark.anyio
async def test_the_roster_excludes_retired_identities(client, session, alice):
    """
    Pinned deliberately, because the naming makes it look fine by accident.

    `_participants` keys a dict by debate_role over every recorded stint, so
    without the active filter the last row to be visited wins. Real continuation
    identities happen to sort so that the live one lands last — which is luck,
    not a rule. Here the retired identity sorts *after* the live one, so an
    unfiltered roster picks a revoked box whose worktree no longer exists.
    """
    orch, run = await _start(client, session, alice, roles=_roles(), rounds=1)
    run_id = run.id
    # The opening roster stands in for a stint that has already been retired.
    for row in await debate_service.list_participants(session, run_id=run_id):
        await debate_service.deactivate_participant(
            session, run_id=run_id, identity=row.identity
        )

    live = await client.create_participant(
        box_slug="aaa-live", identity="vista-aaa-live", role=ParticipantRole.WORKER
    )
    retired = await client.create_participant(
        box_slug="zzz-retired",
        identity="vista-zzz-retired",
        role=ParticipantRole.WORKER,
    )
    for participant in (live, retired):
        await debate_service.add_participant(
            session, run_id=run_id, participant=participant, debate_role="proposer"
        )
    await debate_service.deactivate_participant(
        session, run_id=run_id, identity=retired.identity
    )

    roster = await orch._participants(session, run_id)

    assert roster["proposer"].identity == live.identity, (
        "the roster picked a revoked identity, which cannot post"
    )


@pytest.mark.anyio
async def test_resume_survives_a_committing_checkpoint(client, session, alice):
    """
    The same expiry trap `run` documents, one frame further up.

    `resume` checkpoints after attaching the roster — deliberately, so a viewer
    sees the debate go live rather than waiting for it to finish. That commit
    expires every ORM object the session holds, the run included, so handing that
    same object to `run` makes its first line async IO in a context that cannot
    await.

    Only reproducible with a checkpoint that really commits. The default is a
    no-op, which is why every other resume test passed while the live task failed
    on its first call.
    """

    async def real_commit(s):
        await s.commit()

    project = await _project(session)
    orch = DebateOrchestrator(client=client, roles=_roles(), checkpoint=real_commit)
    run = await orch.start(
        session,
        project_id=project.id,
        user_id=alice.id,
        topic="why does the knee move?",
        rounds=1,
    )
    run_id = run.id
    await orch.run(session, run)

    run = await debate_service.require_debate(session, run_id)
    await orch.resume(session, run=run, extra_rounds=1)

    refreshed = await debate_service.require_debate(session, run_id)
    assert refreshed.rounds == 2
    assert refreshed.rounds_done == 2


@pytest.mark.anyio
async def test_resume_does_not_post_through_a_roster_left_by_a_crash(
    client, session, alice
):
    """
    A resume that died after its checkpoint leaves a live roster behind.

    Retrying then has two attached rosters for the same three roles, and
    `_participants` — a dict keyed by role — keeps whichever identity it visits
    last. So which of them the debate speaks as would be decided by string
    ordering. Asserted on who actually posted, because merely checking the
    orphan ends up inactive proves nothing: `run` retires whatever roster it
    used on its way out, orphan included.
    """
    orch, run = await _start(client, session, alice, roles=_roles(), rounds=1)
    run_id = run.id
    await orch.run(session, run)

    # Stand in for the crashed attempt: a roster attached and committed, then
    # nothing further. Named so it sorts *after* a real stint identity, which is
    # what an unguarded `_participants` would then prefer.
    for role, forum_role in list(ROSTER.items()):
        participant = await client.create_participant(
            box_slug=f"{role}-orphan", identity=f"vista-{role}-orphan", role=forum_role
        )
        await debate_service.add_participant(
            session, run_id=run_id, participant=participant, debate_role=role
        )

    before = {
        p.post_id for p in await debate_service.list_posts(session, run_id=run_id)
    }
    run = await debate_service.require_debate(session, run_id)
    await orch.resume(session, run=run, extra_rounds=1)

    fresh = [
        p
        for p in await debate_service.list_posts(session, run_id=run_id)
        if p.post_id not in before and p.sender != "human"
    ]
    assert fresh, "continuing should have produced posts"
    assert not any(p.sender.endswith("-orphan") for p in fresh), (
        "the debate spoke as a roster left behind by a crashed attempt"
    )


@pytest.mark.anyio
async def test_a_participant_h5i_has_already_dropped_is_still_recorded_as_retired(
    client, session, alice, monkeypatch
):
    """
    Taking a role off the forum and recording that are two different things.

    The commonest refusal is h5i having already revoked the identity — a debate
    that crashed after the subprocess ran but before the row was written. Under a
    single `try`, that refusal skipped the write and the row said `active`
    forever, so every later roster read inherited it and a resumed debate could
    speak as a participant that no longer exists.
    """
    orch, run = await _start(client, session, alice, roles=_roles(), rounds=1)
    run_id = run.id
    roster = await orch._participants(session, run_id)

    async def already_gone(participant):
        raise RuntimeError("no participant matching that identity")

    monkeypatch.setattr(client, "remove_participant", already_gone)
    await orch._retire(session, run_id, roster.values())

    rows = await debate_service.list_participants(session, run_id=run_id)
    assert rows and not any(row.active for row in rows), (
        "h5i refusing the removal left our own record claiming they are attached"
    )


@pytest.mark.anyio
async def test_resume_clears_every_attached_stint_not_just_one_per_role(
    client, session, alice
):
    """
    A debate can have more than one stint attached at once.

    Each crashed resume leaves a live roster, so they accumulate. Retiring
    through a role-keyed dict clears exactly one identity per role however many
    are attached, which means the next resume starts from the same mess — and a
    role can still resolve to a participant whose box is gone.
    """
    orch, run = await _start(client, session, alice, roles=_roles(), rounds=1)
    run_id = run.id
    await orch.run(session, run)

    # Two crashed attempts' worth of rosters, all still marked attached.
    for stint in ("a", "b"):
        for role, forum_role in list(ROSTER.items()):
            participant = await client.create_participant(
                box_slug=f"{role}-{stint}",
                identity=f"vista-{role}-{stint}",
                role=forum_role,
            )
            await debate_service.add_participant(
                session, run_id=run_id, participant=participant, debate_role=role
            )
    assert (
        len(await debate_service.list_active_participants(session, run_id=run_id)) == 6
    )

    run = await debate_service.require_debate(session, run_id)
    await orch.resume(session, run=run, extra_rounds=1)

    stale = [
        p.identity
        for p in await debate_service.list_active_participants(session, run_id=run_id)
        if p.identity.endswith("-a") or p.identity.endswith("-b")
    ]
    assert not stale, f"left attached after a resume: {stale}"


@pytest.mark.anyio
async def test_the_forum_carries_the_proposers_own_words(client, session, alice):
    """
    End to end: what lands on the thread is the text the role wrote.

    The unit test pins `to_post_body`; this pins that nothing between the role and
    the forum re-wraps it. The Reviewer and Referee read post bodies, so a
    scaffold added anywhere on this path would be what they see — and would be
    what the next round is written against.
    """
    orch, run = await _start(client, session, alice, roles=_roles(), rounds=1)
    run = await orch.run(session, run)

    posts = {p.kind: p for p in await debate_service.list_posts(session, run_id=run.id)}
    assert posts[PostKind.PROPOSAL].body == HYPOTHESIS["note"]


@pytest.mark.anyio
async def test_nothing_is_held_uncommitted_while_a_role_speaks(
    engine, client, session, alice
):
    """
    The property the round-boundary checkpoint exists for.

    A role can now block for as long as a cluster job takes, and while it does the
    debate's session must not be sitting on an open write transaction — on SQLite
    that stops the campaign monitor recording the result the role is waiting for.

    Checked from a *separate* session, mid-turn: if the projection is visible
    there, it was committed before the role was handed control.
    """
    from sqlmodel.ext.asyncio.session import AsyncSession

    seen: list[int] = []

    async def nosy_proposer(messages, info):
        # Runs inside the proposer's turn — the moment that matters.
        async with AsyncSession(engine) as other:
            rows = await debate_service.list_posts(other, run_id=run_id)
            seen.append(len(rows))
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, HYPOTHESIS)]
        )

    orch, run = await _start(
        client,
        session,
        alice,
        roles=_roles(proposer=FunctionModel(nosy_proposer)),
        rounds=1,
    )
    run_id = run.id
    await orch.run(session, run)

    assert seen, "the proposer's turn never ran"
    assert seen[0] >= 1, (
        "another session saw nothing mid-turn, so the thread projection was "
        "still sitting in the debate's uncommitted transaction"
    )


@pytest.mark.anyio
async def test_a_role_announces_itself_before_it_starts_thinking(
    engine, client, session, alice
):
    """
    Announced before the turn, not after, and committed — the reader is on
    another session, and an uncommitted activity is invisible to exactly the
    person it is for.
    """
    from sqlmodel.ext.asyncio.session import AsyncSession

    seen: list[str | None] = []

    async def nosy_proposer(messages, info):
        async with AsyncSession(engine) as other:
            row = await debate_service.require_debate(other, run_id)
            seen.append(row.activity)
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, HYPOTHESIS)]
        )

    orch, run = await _start(
        client,
        session,
        alice,
        roles=_roles(proposer=FunctionModel(nosy_proposer)),
        rounds=1,
    )
    run_id = run.id
    await orch.run(session, run)

    assert seen and seen[0] is not None, "nothing said what the debate was doing"
    assert "Proposer" in seen[0]

    # And cleared when it is over: a stale line is the frozen screen again, with
    # a caption claiming otherwise.
    final = await debate_service.require_debate(session, run_id)
    assert final.activity is None
    assert final.activity_since is None


# --------------------------------------------------------------------------- #
# A spent budget should still produce an argument
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_role_out_of_budget_argues_again_without_its_tools(
    client, session, alice
):
    """
    What four BLOCKED reviewer turns should have been.

    A tool that answers unhelpfully invites being called again, and every call is
    a request — so a role can spend a whole turn searching and post nothing. The
    retry withholds the tools, which makes that impossible, and an argument from
    the thread alone is worth incomparably more than a note saying there isn't one.
    """
    attempts = {"n": 0}

    def flaky_reviewer(messages, info: AgentInfo):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise UsageLimitExceeded("The next request would exceed the request_limit")
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, REFUTE)])

    orch, run = await _start(
        client,
        session,
        alice,
        roles=_roles(reviewer=FunctionModel(flaky_reviewer)),
        rounds=1,
    )
    run = await orch.run(session, run)

    kinds = [p.kind for p in await debate_service.list_posts(session, run_id=run.id)]
    assert PostKind.RISK in kinds, "the retry should have produced a real objection"
    assert PostKind.BLOCKED not in kinds, "and no BLOCKED note, since it recovered"


@pytest.mark.anyio
async def test_a_blocked_note_records_what_the_turn_actually_called(
    client, session, alice
):
    """
    The one post in the record where provenance was empty, and the one where a
    reader most needs it: "it spent its whole budget" and "we will not say on
    what" is exactly backwards. Answering why the Reviewer failed took a round of
    guesswork that this would have made unnecessary.
    """
    from vista_backend.agents.forum.grounding import Grounding, build_toolsets

    async def rag(query, kb_slug, n):
        return "nothing useful"

    searched = {"n": 0}

    def searches_then_runs_dry(messages, info: AgentInfo):
        # One real tool call, then out of budget — the shape of the failure.
        searched["n"] += 1
        if searched["n"] == 1:
            return ModelResponse(
                parts=[ToolCallPart("search_literature", {"query": "redox window"})]
            )
        raise UsageLimitExceeded("The next request would exceed the request_limit")

    roles = RoleAgents(
        models={
            "proposer": scripted(HYPOTHESIS),
            "reviewer": FunctionModel(searches_then_runs_dry),
            "referee": scripted(VERDICT),
        },
        toolsets=build_toolsets(Grounding(rag=rag)),
    )
    orch, run = await _start(client, session, alice, roles=roles, rounds=1)
    run = await orch.run(session, run)

    posts = {p.kind: p for p in await debate_service.list_posts(session, run_id=run.id)}
    blocked = posts.get(PostKind.BLOCKED)
    assert blocked is not None, "the reviewer should have posted BLOCKED"
    assert [t["tool"] for t in blocked.tools_used] == ["search_literature"], (
        "the note has to say what the turn spent its budget on"
    )
