"""
Drive the debate loop end to end, with fakes only at the two real boundaries.

The forum is the in-memory `FakeForumClient` (the real client has its own
real-git suite), so the actual loop and the actual projection run; the models are
`FunctionModel`s returning scripted outputs. What is under test is therefore the
whole path — loop, service, tables — with no LLM, no sandbox and no network.

Follows the shape of `test_campaign_driver.py`: script the model, drive the real
runtime, assert the state transitions.
"""

import json
import uuid
import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.models.function import AgentInfo, FunctionModel

from sqlmodel.ext.asyncio.session import AsyncSession

from vista_backend.agents.forum.debate import ROSTER, DebateOrchestrator, identity_for
from vista_backend.agents.forum.roles import RoleAgents
from vista_backend.db.schemas import ProjectTable
from vista_backend.services import debate as debate_service
from vista_backend.services.forum_git import Participant, PostKind


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


def structured(payload: dict) -> ModelResponse:
    """
    A role's answer in the shape prompted output actually produces: JSON as text.

    The fakes used to return `ToolCallPart(info.output_tools[0].name, …)`, which
    modelled pydantic-ai's tool-based output. The agents no longer use it — a model
    with unreliable tool-calling answered in prose and the prose was parsed as
    JSON — so a fake that still calls an output tool would be testing a path
    production does not take.
    """
    return ModelResponse(parts=[TextPart(json.dumps(payload))])


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
        return structured(payload)

    return FunctionModel(respond)


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


def _roles(
    *, proposer=None, reviewer=None, referee=None, capture=None, toolsets=None
) -> RoleAgents:
    """The three roles, each built on its own scripted model."""
    return RoleAgents(
        models={
            "proposer": proposer or scripted(HYPOTHESIS, capture=capture),
            "reviewer": reviewer or scripted(REFUTE),
            "referee": referee or scripted(VERDICT),
        },
        toolsets=toolsets,
    )


@pytest.mark.anyio
async def test_the_roster_records_the_tools_the_debate_actually_ran_with(
    client, session, alice
):
    """
    Whoever runs the debate writes the grants, because only they know them.

    The roster is attached by `open_debate`, and at that moment the run does not
    exist — so `build_run_grounding`, which needs it to find the project's
    knowledge bases and the opener's credentials, cannot have been called. The
    grants recorded there are a bare orchestrator's. Left alone, they made the
    roster panel report two tools for a debate whose posts cite a literature
    search, an attached paper and a commissioned job.
    """
    from vista_backend.agents.forum.grounding import Grounding, build_toolsets

    _, run = await _start(client, session, alice, roles=_roles(), rounds=1)
    run_id = run.id

    before = await debate_service.list_participants(session, run_id=run_id)
    assert {r.debate_role: list(r.granted_tools) for r in before} == {
        "proposer": [],
        "reviewer": [],
        "referee": [],
    }, "a groundingless orchestrator has nothing to grant"

    async def rag(query, kb_slug, n_results):  # pragma: no cover - never called
        return "nothing"

    wired = DebateOrchestrator(
        client=client,
        roles=_roles(toolsets=build_toolsets(Grounding(rag=rag, forum=client))),
        checkpoint=_committing,
        knowledge_bases=["salt"],
    )
    await wired.run(session, await debate_service.require_debate(session, run_id))

    after = await debate_service.list_participants(session, run_id=run_id)
    granted = {r.debate_role: list(r.granted_tools) for r in after}
    assert "search_literature" in granted["reviewer"]
    assert "prior_debates" in granted["reviewer"]


async def _run_to_verdict(client, session, run_id):
    """Argue a debate out, so the next call continues rather than starts."""
    plain = DebateOrchestrator(client=client, roles=_roles(), checkpoint=_committing)
    await plain.run(session, await debate_service.require_debate(session, run_id))


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
    """Three roles, three voices on the thread."""
    orch, run = await _start(client, session, alice, roles=_roles(), rounds=1)
    run = await orch.run(session, run)

    posts = await debate_service.list_posts(session, run_id=run.id)
    by_kind = {p.kind: p for p in posts}
    assert by_kind[PostKind.PROPOSAL].sender.startswith("vista-proposer-")
    assert by_kind[PostKind.RISK].sender.startswith("vista-reviewer-")
    assert by_kind[PostKind.DONE].sender.startswith("vista-referee-")
    assert by_kind[PostKind.PROPOSAL].forum_role == "proposer"
    assert by_kind[PostKind.RISK].forum_role == "reviewer"
    assert by_kind[PostKind.DONE].forum_role == "referee"


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
    Closing is enforced by the forum, not by the loop: the next post is simply
    refused.
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
    """The record should hold the debate up to the stop, plus the CLOSED marker."""
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
    assert {r.debate_role: r.identity for r in rows} == {
        role: identity_for(role, run.id) for role in ROSTER
    }
    assert all(r.forum_role == r.debate_role for r in rows)
    assert run.status == "debating"


@pytest.mark.anyio
async def test_the_roster_outlives_the_debate(client, session, alice):
    """
    Nothing is revoked at the end: a late simulation result still posts under
    the identity that asked for it, and a continued debate speaks as before.
    """
    orch, run = await _start(client, session, alice, roles=_roles(), rounds=1)
    run = await orch.run(session, run)

    rows = await debate_service.list_participants(session, run_id=run.id)
    assert rows and all(r.active for r in rows)


@pytest.mark.anyio
async def test_participants_are_named_after_their_run(client, session, alice):
    """Several debates share one forum, so identities cannot be bare role names."""
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
        return ModelResponse(parts=[TextPart(json.dumps(HYPOTHESIS))])

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
            # once written only to an attachment nothing here could open.
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
async def test_a_continued_debate_reuses_its_identities(client, session, alice):
    """
    One roster for the debate's whole life.

    Continuing is the same debate, so the same three voices: the extra rounds
    post under the identities the earlier rounds used, and no new rows appear.
    """
    orch, run = await _start(client, session, alice, roles=_roles(), rounds=1)
    run_id = run.id
    await orch.run(session, run)

    before = {
        p.identity
        for p in await debate_service.list_participants(session, run_id=run_id)
    }
    earlier = {
        p.post_id for p in await debate_service.list_posts(session, run_id=run_id)
    }

    run = await debate_service.require_debate(session, run_id)
    await orch.resume(session, run=run, extra_rounds=2)

    after = {
        p.identity
        for p in await debate_service.list_participants(session, run_id=run_id)
    }
    assert after == before, "a continued debate keeps its roster"

    fresh = [
        p
        for p in await debate_service.list_posts(session, run_id=run_id)
        if p.post_id not in earlier and p.sender != "human"
    ]
    assert fresh, "continuing should have produced posts"
    assert {p.sender for p in fresh} <= before

    refreshed = await debate_service.require_debate(session, run_id)
    assert refreshed.rounds == 3, "the budget is raised, not reset"
    assert refreshed.rounds_done == 3, "and the extra rounds actually ran"


@pytest.mark.anyio
async def test_the_roster_excludes_retired_identities(client, session, alice):
    """
    Rows written before the git forum can be inactive (retired h5i stints).

    `_participants` keys a dict by debate_role, so without the active filter the
    last row visited would win. Here the inactive identity sorts *after* the
    live one, so an unfiltered roster would pick it.
    """
    orch, run = await _start(client, session, alice, roles=_roles(), rounds=1)
    run_id = run.id
    live = Participant(identity=identity_for("proposer", run_id), role="proposer")

    retired = await debate_service.add_participant(
        session,
        run_id=run_id,
        participant=Participant(identity="vista-proposer-zzz-2", role="proposer"),
        debate_role="proposer",
    )
    retired.active = False
    session.add(retired)
    await session.flush()

    roster = await orch._participants(session, run_id)
    assert roster["proposer"].identity == live.identity


@pytest.mark.anyio
async def test_resume_survives_a_committing_checkpoint(client, session, alice):
    """
    The same expiry trap `run` documents, one frame further up.

    `resume` checkpoints before arguing — deliberately, so a viewer
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
        return ModelResponse(parts=[TextPart(json.dumps(HYPOTHESIS))])

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
        return ModelResponse(parts=[TextPart(json.dumps(HYPOTHESIS))])

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
        return structured(REFUTE)

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


@pytest.mark.anyio
async def test_prose_where_structured_output_was_expected_costs_one_round(
    client, session, alice
):
    """
    The failure that killed a whole debate: a model answered in prose, pydantic-ai
    parsed the prose as JSON, and the resulting error escaped `run_round`, escaped
    `run`, and marked the run `failed` with nothing in the thread to say why.

    Only the budget case was caught. This is the same consequence — the role said
    nothing this round — so it is caught alongside it, and the debate goes on.
    """
    rounds_seen: list[int] = []

    def answers_in_prose(messages, info: AgentInfo):
        # Exactly what gpt-oss-120b did: a good reply, in the wrong envelope.
        rounds_seen.append(len(rounds_seen))
        return ModelResponse(
            parts=[
                TextPart(
                    "The viscosity data from ORNL's FLiBe assessment shows that "
                    "raising the BeF2 fraction pushes pressure drops past 2 MPa."
                )
            ]
        )

    orch, run = await _start(
        client,
        session,
        alice,
        roles=_roles(reviewer=FunctionModel(answers_in_prose)),
        rounds=2,
    )
    run = await orch.run(session, run)

    posts = await debate_service.list_posts(session, run_id=run.id)
    kinds = [p.kind for p in posts]

    assert run.status != "failed", "one malformed turn should not kill the debate"
    assert PostKind.BLOCKED in kinds, "and the thread should say the turn failed"
    assert kinds.count(PostKind.PROPOSAL) == 2, "both rounds still ran"
    assert PostKind.DONE in kinds, "and the referee still ruled"

    blocked = next(p for p in posts if p.kind == PostKind.BLOCKED)
    assert "could not produce an answer in the form" in blocked.body, (
        "a malformed answer is not a spent budget, and the note should not say so"
    )


@pytest.mark.anyio
async def test_a_thread_deleted_mid_debate_ends_it_without_an_error(
    client, session, alice
):
    """Someone deletes the branch on the forge while the debate is arguing."""
    deleted = {"done": False}

    async def delete_after_first_post(post):
        if not deleted["done"]:
            deleted["done"] = True
            client.delete_thread(post.thread)

    orch, run = await _start(
        client,
        session,
        alice,
        roles=_roles(),
        rounds=3,
        on_post=delete_after_first_post,
    )
    run = await orch.run(session, run)

    assert run.thread_missing is True
    assert run.status == "failed"
    assert run.activity is None
    assert await debate_service.list_posts(session, run_id=run.id), "stored posts stay"


@pytest.mark.anyio
async def test_a_peers_close_ends_the_debate(client, session, alice):
    closed = {"done": False}

    async def peer_closes(post):
        if not closed["done"]:
            closed["done"] = True
            client.peer_close(post.thread)

    orch, run = await _start(
        client, session, alice, roles=_roles(), rounds=3, on_post=peer_closes
    )
    run = await orch.run(session, run)
    posts = await debate_service.list_posts(session, run_id=run.id)
    assert run.status == "closed"
    assert debate_service.closed_by(posts) == "peer"
