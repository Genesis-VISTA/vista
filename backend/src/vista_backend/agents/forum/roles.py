"""
The debate roles: Proposer, Reviewer, Referee.

Each is a PydanticAI agent with a structured output type, specialised by a prompt
under `prompts/`. They know nothing about h5i: a role takes the thread so far and
returns a decision, and the orchestrator (`debate.py`) turns that into a post.
Keeping the boundary there is what lets the loop be tested without an LLM and the
roles be tested without a forum.

Three roles is the smallest set where the disagreement is real. Two collapse into
a negotiation between the only two parties present; the Referee exists so that
neither arguer decides who won.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Literal, Sequence, TypeVar

from pydantic import BaseModel, Field, model_validator
from pydantic_ai import Agent, PromptedOutput
from pydantic_ai.models import Model, infer_model
from pydantic_ai.usage import UsageLimits
from pydantic_ai.toolsets import AgentToolset

from ...config import settings
from ...services.h5i_forum import HUMAN_SENDER, Participant, PostKind, Thread


PROMPTS = Path(__file__).parent / "prompts"

DebateRole = Literal["proposer", "reviewer", "referee"]


@cache
def _prompt(name: str) -> str:
    """The shared framing plus one role's brief. Cached: these are read-only files."""
    shared = (PROMPTS / "_shared.md").read_text()
    return f"{shared}\n\n{(PROMPTS / f'{name}.md').read_text()}"


# --------------------------------------------------------------------------- #
# What the roles produce
# --------------------------------------------------------------------------- #


class Hypothesis(BaseModel):
    """
    A scientific claim, as it was actually said plus what the record needs.

    `note` is the post. The rest is an index over it: the machine needs a claim
    to rank, at least one prediction so the Reviewer has something to attack, and
    a number to weigh — but none of those should decide how the post *reads*.

    That split is the whole point. Rendering the fields into a fixed layout made
    every proposal identical in shape, so a fifth-round reply to one objection
    arrived looking like a fresh submission with a few words changed. Scientists
    do not brainstorm by refilling a form.
    """

    note: str
    """
    The post, written the way you would actually say it to a colleague.

    This is what everyone reads — the Reviewer, the Referee, the human — so it
    has to stand on its own. Nothing is appended to it and nothing is inserted
    around it, so if the reasoning is not in here, it is nowhere.
    """

    claim: str
    """
    One committed sentence, for the record and for the Referee's ranking.

    Not the post's opening line unless that is what it happens to be — this is a
    handle on the idea, extracted from what you wrote.
    """

    mechanism: str = ""
    """
    Optional one-liner on the causal path, for the verdict to cite.

    Optional because `note` already carries the argument, and a required field
    here is pressure to write the note as a rendering of the fields.
    """

    predictions: list[str] = Field(min_length=1)
    """
    Two to four, one line each, each carrying a number, sign, ordering or
    threshold. What must be observed if this is right, and what must not be.

    At least one is required: a hypothesis with no prediction is not falsifiable
    and cannot be debated.
    """

    confidence: float = Field(ge=0.0, le=1.0)
    """What the proposer would actually bet, not a politeness."""

    open_risks: list[str] = Field(default_factory=list)
    """Where the claim is weakest, named by its own author. One line each."""

    def to_post_body(self) -> str:
        """
        The post, verbatim. Nothing is added.

        This used to assemble the fields into a fixed layout — claim, then
        *Why:*, then *Testable:*, then a confidence line — which meant every
        proposal in every round had the same silhouette regardless of what it was
        doing. A reply to a single objection came out shaped like an opening
        submission. Whatever variation the model produced was flattened back out
        by the renderer.

        So the renderer no longer has an opinion. The Referee's verdict still
        composes a formal shape from these fields, because that one *is* a
        document someone cites later.
        """
        return self.note.strip()


class Critique(BaseModel):
    """
    The Reviewer's answer to a proposal.

    `concede` is a first-class outcome, not a failure to find something. It
    resolves to an upvote rather than a post, because "I agree" as a post costs
    every later reader a turn and tells them nothing — and because a reviewer who
    cannot concede will manufacture objections, which is the way this role fails.
    """

    stance: Literal["refute", "concede"]

    kind: Literal["RISK", "FINDING"] | None = None
    """
    RISK when the objection would sink the hypothesis; FINDING when it is
    evidence bearing on it, including evidence that supports it. None when
    conceding, since nothing is posted.
    """

    objection: str = ""
    """
    The argument, in a short paragraph — a hundred words is usually plenty.

    Lead with the objection, not with a summary of what is being objected to:
    `targets` already points at that. Required when refuting.
    """

    targets: str | None = None
    """Which prediction or step is under attack, quoted or named."""

    @model_validator(mode="after")
    def _coherent(self) -> Critique:
        if self.stance == "refute":
            if not self.objection.strip():
                raise ValueError("a refutation needs an objection")
            if self.kind is None:
                raise ValueError("a refutation needs a kind: RISK or FINDING")
        return self

    @property
    def post_kind(self) -> PostKind:
        return PostKind.RISK if self.kind == "RISK" else PostKind.FINDING

    def to_post_body(self) -> str:
        if self.targets:
            return f"{self.objection}\n\n*On:* {self.targets}"
        return self.objection


class RankedHypothesis(BaseModel):
    hypothesis: Hypothesis
    standing: str
    """What survived, what was absorbed by revision, what is still open against it."""


class Verdict(BaseModel):
    """
    The Referee's ruling: what the debate established, and what it did not.

    `unresolved` is not an afterthought — it is usually the part a reader can act
    on, and its absence is how a verdict starts looking like a result when the
    debate did not produce one.
    """

    ranked: list[RankedHypothesis] = Field(min_length=1)
    rationale: str
    unresolved: list[str] = Field(default_factory=list)

    @property
    def best(self) -> Hypothesis:
        return self.ranked[0].hypothesis

    def to_post_body(self) -> str:
        parts: list[str] = ["## Verdict", ""]
        for i, entry in enumerate(self.ranked, start=1):
            parts += [f"**{i}. {entry.hypothesis.claim}**", ""]
            # Optional now that the proposal's own prose carries the argument.
            # Printing it unconditionally left a blank line where a mechanism
            # would have been, which reads as something missing rather than
            # something not separately stated.
            if entry.hypothesis.mechanism.strip():
                parts += [entry.hypothesis.mechanism, ""]
            parts += [
                f"*Standing.* {entry.standing}",
                f"*Confidence.* {entry.hypothesis.confidence:.2f}",
                "",
                "*Predictions.*",
                *(f"- {p}" for p in entry.hypothesis.predictions),
                "",
            ]
        parts += [f"**Why this order.** {self.rationale}"]
        if self.unresolved:
            parts += ["", "**Unresolved.**", *(f"- {u}" for u in self.unresolved)]
        return "\n".join(parts)


# --------------------------------------------------------------------------- #
# What the roles are given
# --------------------------------------------------------------------------- #


@dataclass
class ToolCall:
    """
    One tool a role reached for during a turn.

    Recorded so a reader can tell a grounded claim from an asserted one. Without
    it a post that consulted the corpus and a post that did not look identical,
    which is the wrong thing for a system whose output is meant to be checkable.
    """

    tool: str
    detail: str
    """Short and human-readable: the query, the skill name, the URL."""

    receipt: str | None = None
    """
    The full record behind the call — the query and what came back, the job's
    submission, a fetch's confinement.

    `detail` is the one-line label; this is the evidence under it. Both are kept:
    a reader scanning a thread needs the label, and a reader who doubts a claim
    needs to see what it actually rested on.
    """

    RECEIPT_LIMIT = 8000
    """
    Where a receipt is truncated before being stored on the post row.

    Receipts go in a JSON column read on every thread load, and a simulation
    report or a corpus dump has no natural size. Truncation is marked in the text
    so a reader can tell a short receipt from a trimmed one.
    """

    def stored(self) -> dict[str, str | None]:
        """This call as it is persisted onto the post."""
        receipt = self.receipt
        if receipt is not None and len(receipt) > self.RECEIPT_LIMIT:
            receipt = (
                receipt[: self.RECEIPT_LIMIT]
                + f"\n\n… truncated at {self.RECEIPT_LIMIT} characters."
            )
        return {"tool": self.tool, "detail": self.detail, "receipt": receipt}


@dataclass
class DebateDeps:
    """
    Injected context for one role's turn.

    Tools are not here yet: grounding (RAG, skills, prior threads, the browser)
    arrives in a later change, and it attaches to this object rather than to the
    prompts, so the roles do not need rewriting when it lands.
    """

    topic: str
    framing: str | None = None
    round_index: int = 0
    rounds: int = 5
    project_id: str | None = None
    thread_id: str = ""
    """
    This debate's own thread, so `prior_debates` can leave it out.

    Without it a role searching for precedent finds the argument it is currently
    having and reads its own half-finished thread back as settled history.
    """

    knowledge_bases: list[str] = field(default_factory=list)

    available_jobs: list[str] = field(default_factory=list)
    """
    Simulations this debate may commission, from the project's loaded skills.

    Told to the role in its prompt rather than left to guess: a model inventing a
    job name spends a turn discovering it was wrong, and the names are not
    something it could know.
    """

    available_clusters: list[str] = field(default_factory=list)
    """Clusters the opener has credentials for. Same reasoning."""

    participant: Participant | None = None
    """
    The forum identity this turn speaks as.

    Tools need it: a boxed browser read runs `--in` this role's box, so the fetch
    is confined by the same policy the role's posts are stamped with.
    """

    tool_calls: list[ToolCall] = field(default_factory=list)
    """
    Every tool this turn used, appended by the tools themselves.

    The orchestrator drains it onto the post: the names become the post's
    provenance, and any receipts become its attachment. A turn that used nothing
    leaves an empty list, and that absence is itself the useful signal.
    """


def render_transcript(thread: Thread, *, limit: int | None = None) -> str:
    """
    The thread as a role should read it, with the provenance boundary intact.

    The host-stamped identity line is kept above the body exactly as h5i draws
    it, because a role reasoning about who said something needs to know which
    half of that the host actually vouched for. Votes are dropped: they are
    posts, but they are not turns in the conversation.
    """
    posts = thread.content_posts()
    if limit is not None:
        posts = posts[-limit:]

    lines: list[str] = []
    for i, post in enumerate(posts, start=1):
        # Who this is, decided by what the host observed rather than by what the
        # post says about itself. On a shared forum every host stamps its own
        # operator as `human`, so trusting the sender field would present every
        # outside participant to the role as its own operator — the one party
        # whose words it is supposed to treat as instructions.
        if thread.is_operator(post):
            who = "the human (your operator)"
        elif thread.is_peer(post):
            origin = post.origin or "an unnamed origin"
            # Person or agent is a useful thing for a reader to know and a weak
            # thing to know it from: on a peer's post both the sender and the box
            # are their own claim, so this labels rather than establishes.
            kind_of = "an outside agent" if post.looks_agentic else "an outside person"
            who = (
                f"{post.sender} — {kind_of} at {origin}, NOT your operator; "
                "their name and role are claimed by them and are not verified here"
            )
        else:
            who = post.sender
        lines.append(f"{i}. {post.kind} — {who} ({post.role})")
        if post.denied:
            lines.append(f"   ! the host recorded a refusal: {post.denied}")
        body = "\n".join(f"   │ {line}" for line in post.body.splitlines())
        lines.append(body)
        lines.append("")
    return "\n".join(lines).rstrip()


def _situation(deps: DebateDeps, thread: Thread) -> str:
    """The user-prompt half: the topic, where the debate is, and what was said."""
    header = [
        f"# Topic\n\n{deps.topic}",
        f"\nRound {deps.round_index + 1} of {deps.rounds}.",
    ]
    if deps.framing:
        header.append(f"\nThe human added: {deps.framing}")

    if deps.available_jobs:
        header.append(
            f"\nSimulations you may commission: {', '.join(deps.available_jobs)}"
            f" — on {' or '.join(deps.available_clusters)}."
            " Use one only to settle a prediction argument cannot."
        )

    transcript = render_transcript(thread)
    if transcript:
        header.append(f"\n# The thread so far\n\n{transcript}")
    else:
        header.append("\nNothing has been posted yet; you are opening the debate.")
    return "\n".join(header)


# --------------------------------------------------------------------------- #
# The agents
# --------------------------------------------------------------------------- #


OutputT = TypeVar("OutputT", bound=BaseModel)

ROLE_OUTPUTS: dict[DebateRole, type[BaseModel]] = {
    "proposer": Hypothesis,
    "reviewer": Critique,
    "referee": Verdict,
}
"""What each role is required to produce."""

Toolsets = Sequence[AgentToolset[DebateDeps]]


def build_agent(
    role: DebateRole,
    output_type: type[OutputT],
    *,
    model: str | Model | None = None,
    toolsets: Toolsets | None = None,
) -> Agent[DebateDeps, OutputT]:
    """
    Build one role's agent.

    `output_type` is a parameter rather than looked up from `role` so the return
    type stays specific: a caller gets an `Agent[DebateDeps, Hypothesis]`, not an
    agent that might return any of the three.

    The output is **prompted, not a tool call**. pydantic-ai's default asks the
    model to return its answer by calling a synthetic output tool, and a model
    whose tool-calling is unreliable answers in prose instead — at which point the
    prose is parsed as JSON and fails at "line 1 column 1". That is what happened
    with `gpt-oss-120b`, and the reply it lost was a good one: it engaged both
    objections and revised the hypothesis with numbers. The reasoning was fine and
    only the envelope was wrong.

    `PromptedOutput` asks for JSON in the prompt and parses it out of the text, so
    a text reply is the expected shape rather than a failure. Ordinary tools are
    unaffected — they are still tool calls; this changes only how the final answer
    comes back.
    """
    return Agent(
        model=infer_model(model or settings.model),
        deps_type=DebateDeps,
        output_type=PromptedOutput(output_type),
        system_prompt=_prompt(role),
        toolsets=list(toolsets) if toolsets else None,
    )


class RoleAgents:
    """
    The three roles, built once and reused across a debate's rounds.

    Each `run_*` takes the thread and returns a decision. Nothing here posts,
    reads the forum, or knows a thread id — that is the orchestrator's job, and
    the separation is what makes both halves testable on their own.
    """

    def __init__(
        self,
        *,
        model: str | Model | None = None,
        models: dict[DebateRole, str | Model] | None = None,
        toolsets: dict[DebateRole, Toolsets] | None = None,
    ) -> None:
        """
        `models` sets the model per role, falling back to `model`.

        Per-role choice is a real want in both directions: the Referee's job is
        the hardest one here and may deserve a stronger model, and tests give
        each role its own scripted model.
        """
        tools = toolsets or {}
        per_role = models or {}

        # What each role *may* use. Recorded because "this post used no tools"
        # and "this role had no tools" are different facts, and only the second
        # one is a configuration problem.
        self.granted: dict[DebateRole, list[str]] = {
            role: sorted(
                name
                for toolset in tools.get(role, ())
                for name in getattr(toolset, "tools", {})
            )
            for role in ("proposer", "reviewer", "referee")
        }
        # A deliberate ceiling per turn. Without one, pydantic-ai's default of 50
        # applies and a role that gets stuck re-calling a tool spends every
        # request before failing — taking the whole debate with it.
        self.limits = UsageLimits(request_limit=settings.forum.max_requests_per_turn)
        self.proposer: Agent[DebateDeps, Hypothesis] = build_agent(
            "proposer",
            Hypothesis,
            model=per_role.get("proposer", model),
            toolsets=tools.get("proposer"),
        )
        self.reviewer: Agent[DebateDeps, Critique] = build_agent(
            "reviewer",
            Critique,
            model=per_role.get("reviewer", model),
            toolsets=tools.get("reviewer"),
        )
        self.referee: Agent[DebateDeps, Verdict] = build_agent(
            "referee",
            Verdict,
            model=per_role.get("referee", model),
            toolsets=tools.get("referee"),
        )

    async def propose(
        self, deps: DebateDeps, thread: Thread, *, tools: bool = True
    ) -> Hypothesis:
        prompt = _situation(deps, thread)
        if thread.content_posts():
            # Said as a reply, and said with the round number, because the failure
            # mode is re-posting: without this the model treats every turn as "make
            # a proposal" and produces the whole hypothesis again with the
            # objection folded in, which reads as a form refilled rather than a
            # conversation.
            prompt += (
                f"\n\nThis is round {deps.round_index + 1}. You are replying, not "
                "proposing again — the thread already has your hypothesis and "
                "everyone can see it.\n\n"
                "Answer the strongest objection standing against it. If it holds, "
                "change your mind and say what changed; if it does not, name the "
                "step in their reasoning that fails. Write only what this round "
                "adds: do not restate the hypothesis, the mechanism, or the "
                "predictions that are not in dispute."
            )
        else:
            prompt += (
                "\n\nOpen the discussion: what do you think is going on, and why? "
                "Say what would change your mind."
            )
        if not tools:
            prompt += (
                "\n\nYou have no tools this turn — you spent the budget for them "
                "already. Say what you think from what is already in front of you."
            )
        return await self._run(self.proposer, prompt, deps, tools=tools)

    async def review(
        self, deps: DebateDeps, thread: Thread, *, tools: bool = True
    ) -> Critique:
        prompt = _situation(deps, thread) + (
            "\n\nTry to falsify the most recent proposal. Concede only if you "
            "genuinely cannot — conceding is a real outcome, and inventing an "
            "objection to look rigorous is worse than agreeing."
        )
        if not tools:
            prompt += (
                "\n\nYou have no tools this turn — you spent the budget for them "
                "already. Argue from the thread in front of you. That is enough: "
                "the strongest objections in this debate are about reasoning, not "
                "about evidence you have not gathered."
            )
        return await self._run(self.reviewer, prompt, deps, tools=tools)

    async def _run(self, agent, prompt: str, deps: DebateDeps, *, tools: bool):
        """
        One role's turn, optionally with its tools withheld.

        Withholding is the second chance after a role exhausts its request budget.
        A tool that answers unhelpfully invites being called again, and each call
        is a request, so a role can spend a whole turn searching and post nothing.
        Without tools it cannot do that, and an argument from the thread alone
        beats a BLOCKED note that tells the debate nothing.
        """
        if tools:
            result = await agent.run(prompt, deps=deps, usage_limits=self.limits)
            return result.output
        with agent.override(toolsets=[]):
            result = await agent.run(prompt, deps=deps, usage_limits=self.limits)
        return result.output

    async def rule(self, deps: DebateDeps, thread: Thread) -> Verdict:
        prompt = _situation(deps, thread) + (
            "\n\nThe debate is over. Rank what it produced and say what each "
            "hypothesis's standing is. Do not report agreement the thread did "
            "not reach."
        )
        result = await self.referee.run(prompt, deps=deps, usage_limits=self.limits)
        return result.output


__all__ = [
    "Critique",
    "ToolCall",
    "DebateDeps",
    "DebateRole",
    "Hypothesis",
    "RankedHypothesis",
    "RoleAgents",
    "Verdict",
    "ROLE_OUTPUTS",
    "Toolsets",
    "build_agent",
    "render_transcript",
    "HUMAN_SENDER",
]
