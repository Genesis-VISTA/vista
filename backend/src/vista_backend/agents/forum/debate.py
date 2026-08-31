"""
The debate orchestrator: the round loop that turns three role agents into an argument.

The loop is deliberately dumb and deterministic. It owns sequencing — who speaks,
in what order, how many times, and when to stop — and nothing else. All the
judgement lives in the roles, and all the forum mechanics live in the client, so
this module can be driven end to end with fakes and no LLM.

One round is: the Proposer posts, the Reviewer answers it. The thread is re-read
before every turn, which is also how the human's interjections reach the agents —
there is no separate inbox, because the thread already is one.

Two things end a debate. The round budget runs out, and the Referee rules. Or the
human closes the thread, in which case h5i stops accepting posts and this loop
finds out by being refused. That refusal is expected control flow: a debate the
human ended early is a real outcome, and it ends without a verdict rather than
with a manufactured one.
"""

from __future__ import annotations

import logging
import uuid
from typing import Awaitable, Callable, Iterable

from pydantic_ai.exceptions import (
    ToolRetryError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from sqlmodel.ext.asyncio.session import AsyncSession

from ...config import settings
from ...db.schemas import DebateRunTable
from ...services import debate as debate_service
from ...services.h5i_forum import (
    ForumClient,
    Participant,
    ParticipantRole,
    Post,
    PostKind,
    Thread,
    ThreadClosed,
)
from .roles import DebateDeps, DebateRole, RoleAgents
from .simulation import open_simulations


logger = logging.getLogger(__name__)


# The debate's roster. The scientific role is the forum *identity*, which the host
# stamps; h5i's own role vocabulary is only worker/reviewer/observer, so the
# mapping is lossy in that direction and the identity carries the real meaning.
ROSTER: dict[DebateRole, ParticipantRole] = {
    "proposer": ParticipantRole.WORKER,
    "reviewer": ParticipantRole.REVIEWER,
    "referee": ParticipantRole.WORKER,
}


def _participant_of(row) -> Participant:
    """The client's view of a recorded participant row."""
    return Participant(
        identity=row.identity,
        role=ParticipantRole(row.forum_role),
        box_slug=row.box_slug,
        box_id=row.box_id,
        policy_digest=row.policy_digest,
    )


TURN_FAILED: tuple[type[Exception], ...] = (
    UsageLimitExceeded,
    UnexpectedModelBehavior,
    ToolRetryError,
)
"""
Ways a role can fail to produce a usable answer, all of which cost a round.

`UsageLimitExceeded` is a spent request budget. The other two are the model
failing to return the shape asked of it — most often by answering in prose where
structured output was expected, which pydantic-ai then tries to parse as JSON and
reports as "expected value at line 1 column 1".

Caught together because the consequence is identical: this role said nothing this
round. Before, only the budget case was caught, so a malformed answer escaped
`run_round`, escaped `run`, and killed the whole debate — a run marked `failed`
with nothing in the thread to say why. One bad turn should cost a turn.

Deliberately not a bare `except Exception`: a forum that has gone away, a
database that will not write, a bug in this module — those are faults, and
swallowing them into a BLOCKED note would turn every one into a debate that
quietly argued worse.
"""


PostHook = Callable[[Post], Awaitable[None]]
"""Called as each post lands. The API's change feed hangs off this."""

Checkpoint = Callable[[AsyncSession], Awaitable[None]]
"""
Make the debate's progress so far durable.

A debate takes minutes and writes as it goes. Left in one transaction until the
end, none of it is visible to anything reading through another session — which
is every reader that matters: the event stream, another worker, a `GET` while it
runs. The live view would only go live once the debate was over.

Injected rather than assumed, because the orchestrator does not otherwise own
transaction boundaries: tests share one session and pass nothing.
"""


class DebateOrchestrator:
    """
    Drives one debate from an empty thread to a verdict.

    The client and the roles are injected rather than constructed, so tests
    substitute a fake forum and a scripted model and exercise the real loop.
    """

    def __init__(
        self,
        *,
        client: ForumClient,
        roles: RoleAgents | None = None,
        on_post: PostHook | None = None,
        checkpoint: Checkpoint | None = None,
        available_jobs: list[str] | None = None,
        available_clusters: list[str] | None = None,
        knowledge_bases: list[str] | None = None,
    ) -> None:
        self.client = client
        self.roles = roles or RoleAgents()
        self.on_post = on_post
        self.checkpoint = checkpoint
        self.available_jobs = available_jobs or []
        self.available_clusters = available_clusters or []
        self.knowledge_bases = knowledge_bases or []

    # -- setup ------------------------------------------------------------- #

    async def start(
        self,
        session: AsyncSession,
        *,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        topic: str,
        framing: str | None = None,
        rounds: int | None = None,
    ) -> DebateRunTable:
        """
        Open the thread, attach the roster, and record it all.

        Participants are named after the run so several debates can share a
        forum without colliding on box slugs, and so a stray box is traceable to
        the debate that made it.
        """
        thread_id = await self.client.create_thread(topic, body=framing)
        run = await debate_service.create_debate(
            session,
            project_id=project_id,
            user_id=user_id,
            topic=topic,
            thread_id=thread_id,
            framing=framing,
            rounds=rounds or settings.forum.default_rounds,
        )

        for role, forum_role in ROSTER.items():
            slug = f"{role}-{str(run.id)[:8]}"
            participant = await self.client.create_participant(
                box_slug=slug, identity=f"vista-{slug}", role=forum_role
            )
            await debate_service.add_participant(
                session,
                run_id=run.id,
                participant=participant,
                debate_role=role,
                granted_tools=self.roles.granted.get(role, []),
            )

        return await debate_service.set_status(
            session, run_id=run.id, status="debating"
        )

    async def resume(
        self,
        session: AsyncSession,
        *,
        run: DebateRunTable,
        extra_rounds: int,
    ) -> DebateRunTable:
        """
        Put a finished debate back on the forum for another few rounds.

        The reason this is not just `run()` again: the roster was revoked when
        the debate concluded, and a revoked identity cannot post. So a new one is
        attached, under identities suffixed with the stint number — the old posts
        keep the names they were made under, and the thread shows plainly that
        the argument was picked up again rather than pretending it never stopped.

        `rounds_done` is left alone and the budget is raised instead, so the loop
        runs exactly the extra rounds asked for and the record still says how
        much arguing this debate has had in total.
        """
        run_id = run.id

        # Clear any roster still marked attached before adding another.
        #
        # Normally there is none: `run` retires on its way out. But a resume that
        # crashed after its checkpoint leaves one committed and active, and two
        # live rosters for the same three roles makes `_participants` pick by
        # string ordering — which of two identities wins should never be decided
        # by how they sort.
        leftover = [
            _participant_of(row)
            for row in await debate_service.list_active_participants(
                session, run_id=run_id
            )
        ]
        if leftover:
            logger.info(
                "debate %s: retiring %d roster entr(ies) left attached",
                run_id,
                len(leftover),
            )
            # Every attached row, not the role-keyed roster: a debate that crashed
            # mid-resume can have more than one stint attached, and a dict keyed by
            # role would keep exactly one of them — leaving the rest marked active
            # for the next resume to trip over in the same way.
            await self._retire(session, run_id, leftover)

        stint = (
            len(await debate_service.list_participants(session, run_id=run_id))
            // len(ROSTER)
            + 1
        )

        for role, forum_role in ROSTER.items():
            slug = f"{role}-{str(run_id)[:8]}-{stint}"
            participant = await self.client.create_participant(
                box_slug=slug, identity=f"vista-{slug}", role=forum_role
            )
            await debate_service.add_participant(
                session,
                run_id=run_id,
                participant=participant,
                debate_role=role,
                granted_tools=self.roles.granted.get(role, []),
            )

        await debate_service.update_debate(
            session,
            run_id=run_id,
            rounds=run.rounds + extra_rounds,
            status="debating",
        )
        # Commit before arguing, so a viewer sees the debate go live instead of
        # waiting for it to finish — and then re-read, because that commit
        # expired the row above. Handing the expired object to `run` makes its
        # first line async IO in a context that cannot await.
        await self._checkpoint(session)
        return await self.run(
            session, await debate_service.require_debate(session, run_id)
        )

    async def _participants(
        self, session: AsyncSession, run_id: uuid.UUID
    ) -> dict[DebateRole, Participant]:
        """Rebuild the client's view of the roster from what was recorded."""
        rows = await debate_service.list_active_participants(session, run_id=run_id)
        roster: dict[DebateRole, Participant] = {}
        for row in rows:
            roster[row.debate_role] = _participant_of(row)  # type: ignore[index]
        return roster

    # -- the loop ---------------------------------------------------------- #

    async def run(self, session: AsyncSession, run: DebateRunTable) -> DebateRunTable:
        """
        Argue to a verdict, or until the human stops it.

        `ThreadClosed` is caught here rather than inside a round because closure
        can land between any two calls — the human is not waiting for a round
        boundary — and every one of those points means the same thing.
        """
        # The loop spans checkpoints, and a checkpoint commits — which expires
        # every ORM object this session is holding. So the run is carried as an
        # id and re-read each time round; holding the row across a commit and
        # then reading `rounds_done` off it is sync IO in an async session, which
        # fails as MissingGreenlet rather than as anything that names the cause.
        run_id = run.id
        roster = await self._participants(session, run_id)
        try:
            while True:
                run = await debate_service.require_debate(session, run_id)
                if run.rounds_done >= run.rounds:
                    break
                await self.run_round(session, run, roster)
            await self.conclude(session, run, roster)
        except ThreadClosed:
            logger.info("debate %s: the human closed the thread", run_id)
            await self._sync(session, run_id)
            await debate_service.set_status(session, run_id=run_id, status="closed")
            await self._checkpoint(session)
        finally:
            await self._retire(session, run_id, roster.values())
            # Whatever ended the debate — verdict, closure, a raised error — the
            # interface must stop saying someone is thinking. A stale activity is
            # worse than none: it is the frozen screen this was added to fix,
            # except now with a caption.
            await debate_service.set_activity(session, run_id=run_id, activity=None)
            await self._checkpoint(session)
        return await debate_service.require_debate(session, run_id)

    async def run_round(
        self,
        session: AsyncSession,
        run: DebateRunTable,
        roster: dict[DebateRole, Participant],
    ) -> DebateRunTable:
        """
        One round: a proposal, and the Reviewer's answer to it.

        The thread is re-read before each turn rather than passed along from the
        previous one, so anything that arrived in between — the human's
        interjection, a vote — is in front of the role when it speaks.
        """
        run_id = run.id
        index = run.rounds_done

        propose_deps = self._turn_deps(run, index, roster["proposer"])
        thread = await self._sync_thread(session, run, round_index=index)
        # Commit the projection before handing off to the role.
        #
        # `_sync_thread` writes, so without this the session holds a write
        # transaction for the whole turn. That was harmless while a turn was
        # seconds; a role that waits for a cluster job holds it for minutes, and
        # on SQLite that blocks the monitor from recording the very result the
        # role is waiting for — the debate would wait on something it was itself
        # blocking.
        run = await self._announce(
            session, run_id, f"Proposer is thinking · round {index + 1} of {run.rounds}"
        )
        try:
            hypothesis = await self._speak(
                self.roles.propose, propose_deps, thread, run, run_id, index, "proposer"
            )
        except TURN_FAILED as exc:
            # The round produced nothing, but
            # the thread should say so rather than the debate ending in a
            # traceback nobody on the forum can see.
            await self._blocked(
                session, run, roster["proposer"], index, exc, deps=propose_deps
            )
            await debate_service.update_debate(
                session, run_id=run_id, rounds_done=index + 1
            )
            await self._checkpoint(session)
            return await debate_service.require_debate(session, run_id)
        proposal = await self._post(
            session,
            run,
            roster["proposer"],
            hypothesis.to_post_body(),
            kind=PostKind.PROPOSAL,
            round_index=index,
            deps=propose_deps,
        )

        review_deps = self._turn_deps(run, index, roster["reviewer"])
        thread = await self._sync_thread(session, run, round_index=index)
        run = await self._announce(
            session, run_id, f"Reviewer is looking for holes · round {index + 1}"
        )
        try:
            critique = await self._speak(
                self.roles.review, review_deps, thread, run, run_id, index, "reviewer"
            )
        except TURN_FAILED as exc:
            # An unanswered proposal is a worse record than an answered one, but
            # it is a true one, and the next round still has something to argue.
            await self._blocked(
                session, run, roster["reviewer"], index, exc, deps=review_deps
            )
            critique = None

        if critique is None:
            pass
        elif critique.stance == "concede":
            # Agreement is a vote, not a post. Saying "I agree" as a post costs
            # every later reader a turn and adds nothing to the record.
            await self.client.vote(roster["reviewer"], run.thread_id, proposal.id)
        else:
            await self._post(
                session,
                run,
                roster["reviewer"],
                critique.to_post_body(),
                kind=critique.post_kind,
                reply_to=proposal.id,
                round_index=index,
                deps=review_deps,
            )

        await debate_service.update_debate(
            session, run_id=run_id, rounds_done=index + 1
        )
        await self._checkpoint(session)
        return await debate_service.require_debate(session, run_id)

    async def conclude(
        self,
        session: AsyncSession,
        run: DebateRunTable,
        roster: dict[DebateRole, Participant],
    ) -> DebateRunTable:
        """The Referee rules on what the thread produced."""
        run_id = run.id
        thread = await self._sync_thread(session, run, round_index=None)
        deps = self._turn_deps(run, run.rounds_done, roster["referee"])
        run = await self._announce(session, run_id, "Referee is ruling on the thread")
        try:
            verdict = await self.roles.rule(deps, thread)
        except TURN_FAILED as exc:
            # No verdict is an honest outcome; a fabricated one is not.
            await self._blocked(session, run, roster["referee"], None, exc)
            await debate_service.set_status(session, run_id=run_id, status="failed")
            await self._checkpoint(session)
            return await debate_service.require_debate(session, run_id)
        await self._post(
            session,
            run,
            roster["referee"],
            verdict.to_post_body(),
            kind=PostKind.DONE,
            round_index=None,
            deps=deps,
        )
        await debate_service.record_verdict(
            session, run_id=run_id, verdict=verdict.model_dump()
        )
        await self._checkpoint(session)
        return await debate_service.require_debate(session, run_id)

    # -- plumbing ---------------------------------------------------------- #

    async def _post(
        self,
        session: AsyncSession,
        run: DebateRunTable,
        participant: Participant,
        body: str,
        *,
        kind: PostKind,
        reply_to: str | None = None,
        round_index: int | None = None,
        deps: DebateDeps | None = None,
    ) -> Post:
        attachment = await self._stage_receipts(participant, deps)
        post = await self.client.post_as(
            participant,
            run.thread_id,
            body,
            kind=kind,
            reply_to=reply_to,
            attachment=attachment,
        )
        await self._sync_thread(session, run, round_index=round_index)
        if deps is not None:
            # After the projection: the row has to exist before provenance can be
            # written onto it. The forum knows what was said; how the agent got
            # there is ours to record.
            await debate_service.record_post_tools(
                session,
                run_id=run.id,
                post_id=post.id,
                tools=[call.stored() for call in deps.tool_calls],
            )
        if self.on_post is not None:
            await self.on_post(post)
        return post

    async def _stage_receipts(
        self, participant: Participant, deps: DebateDeps | None
    ) -> str | None:
        """
        Turn this turn's receipts into an attachment on the post.

        h5i takes one attachment per post, so several calls become one file. A
        refused fetch has a receipt too, and it is kept for the same reason a
        successful one is: what the debate could not reach is part of the record.

        The same receipts are also stored on the post row, and the duplication is
        deliberate — they serve different readers. This copy goes onto the forum,
        where a peer with nothing but the git remote can read it, and it is the
        untruncated one. The row is what VISTA's own interface shows, capped
        because it is read on every thread load.
        """
        receipts = [c.receipt for c in deps.tool_calls if c.receipt] if deps else []
        if not receipts:
            return None
        body = "\n\n".join(receipts)
        try:
            return await self.client.stage_attachment(
                participant, "citations.json", body
            )
        except Exception:  # noqa: BLE001 — a citation must never lose the post
            logger.warning("debate: could not stage receipts", exc_info=True)
            return None

    async def _blocked(
        self,
        session: AsyncSession,
        run: DebateRunTable,
        participant: Participant,
        round_index: int | None,
        exc: Exception,
        deps: DebateDeps | None = None,
    ) -> None:
        """
        Record on the thread that a role ran out of budget mid-turn.

        `deps` is passed so the tool calls the failed turn *did* make are recorded
        on the note. Without them this post was the one place in the record where
        provenance was empty — and it is the one place a reader most needs it,
        because "it spent its whole budget" and "we will not say on what" is
        exactly backwards.

        `BLOCKED` is h5i's kind for exactly this: the agent could not finish, and
        a reader needs to know that rather than inferring silence. Best effort —
        a debate already in trouble must not also fail on its own error report.
        """
        # Say which kind of failure it was. "This is a budget limit" was written
        # when a spent budget was the only thing caught, and it is now sometimes a
        # lie: a model that answered in prose where structured output was expected
        # did not run out of anything.
        if isinstance(exc, UsageLimitExceeded):
            cause = "I ran out of my request budget for this turn"
        else:
            cause = "I could not produce an answer in the form this debate needs"

        try:
            await self._post(
                session,
                run,
                participant,
                f"{cause}: {exc}. "
                "This is a failure of my turn, not a conclusion — nothing here "
                "should be read as agreement or as a finding.",
                kind=PostKind.BLOCKED,
                round_index=round_index,
                deps=deps,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "debate %s: could not post a BLOCKED note", run.id, exc_info=True
            )

    def _turn_deps(
        self, run: DebateRunTable, round_index: int, participant: Participant
    ) -> DebateDeps:
        """Deps for one turn, including what this debate is allowed to run."""
        deps = _deps(run, round_index, participant)
        deps.available_jobs = self.available_jobs
        deps.available_clusters = self.available_clusters
        # Which corpora the search tool may name. Empty means the project has
        # none, in which case the tool was not granted either.
        deps.knowledge_bases = self.knowledge_bases
        return deps

    async def _speak(self, role_fn, deps, thread, run, run_id, index, label):
        """
        One role's turn, retried once with its tools withheld.

        A tool that answers unhelpfully invites being called again, and every call
        is a request — so a role can spend a whole turn searching and post nothing.
        That is what happened to the Reviewer on a forum where `prior_debates`
        could only ever say "no match": four rounds, four BLOCKED notes, no
        critique. Without tools it cannot do that, and an argument from the thread
        alone is worth incomparably more than a note saying there isn't one.

        Only `UsageLimitExceeded` is caught here. Anything else is a real fault
        and belongs in the log, not smoothed over by a quieter second attempt.
        """
        try:
            return await role_fn(deps, thread)
        except UsageLimitExceeded:
            logger.info(
                "debate %s: %s spent its request budget; retrying with no tools",
                run_id,
                label,
            )
            # The first attempt's calls are kept, not cleared. They are what the
            # turn spent its budget on, so they belong on whatever it eventually
            # posts — a provenance chip reading "search_literature — nothing
            # found" next to a tool-free argument is the most useful thing a
            # reader of that post could have.
            return await role_fn(deps, thread, tools=False)

    async def _announce(
        self, session: AsyncSession, run_id: uuid.UUID, activity: str | None
    ) -> DebateRunTable:
        """
        Say what is about to happen, durably, before it happens.

        Committed rather than flushed: the reader is the event stream on another
        session, and an uncommitted activity is invisible to exactly the person
        it is for.
        """
        await debate_service.set_activity(session, run_id=run_id, activity=activity)
        return await self._settle(session, run_id)

    async def _settle(self, session: AsyncSession, run_id: uuid.UUID) -> DebateRunTable:
        """
        Commit what is pending and hand back a live run row.

        Called before a role speaks, so no write transaction is held across a
        turn — a turn can now block on a cluster job. Re-reads because the commit
        expires every object the session holds, and handing the expired one on is
        how this module has produced MissingGreenlet more than once.
        """
        await self._checkpoint(session)
        return await debate_service.require_debate(session, run_id)

    async def _checkpoint(self, session: AsyncSession) -> None:
        """Make progress durable, if the caller gave us a way to. Never fatal."""
        if self.checkpoint is None:
            return
        try:
            await self.checkpoint(session)
        except Exception:  # noqa: BLE001 — a failed checkpoint must not end the debate
            logger.warning("debate: could not checkpoint progress", exc_info=True)

    async def _sync_thread(
        self, session: AsyncSession, run: DebateRunTable, *, round_index: int | None
    ) -> Thread:
        """Read the forum and project it. The read is what makes the DB catch up."""
        thread = await self.client.read_thread(run.thread_id)
        await debate_service.project_thread(
            session, run_id=run.id, thread=thread, round_index=round_index
        )
        return thread

    async def _sync(self, session: AsyncSession, run_id: uuid.UUID) -> None:
        """
        Project whatever the thread ended up holding.

        Called on the closed path so the record includes the human's closing
        posts and h5i's own CLOSED marker, rather than stopping at whatever the
        agents last managed to say.
        """
        try:
            run = await debate_service.require_debate(session, run_id)
            await self._sync_thread(session, run, round_index=None)
        except Exception:  # noqa: BLE001 — a failed final read must not mask the close
            logger.warning("debate %s: could not read the closed thread", run_id)

    async def _retire(
        self,
        session: AsyncSession,
        run_id: uuid.UUID,
        roster: Iterable[Participant],
    ) -> None:
        """
        Take the roster off the forum and delete its boxes.

        Their posts stay and stay attributed — revoking changes who may post
        next, not what was said. Best effort, and never allowed to fail a debate
        that has already reached its conclusion.
        """
        pending = await open_simulations(session, debate_run_id=run_id)
        if pending:
            # A revoked participant cannot post, and a commissioned job's result
            # has to come back under the identity that asked for it. The
            # collector retires the roster once the last job is in.
            logger.info(
                "debate %s: keeping the roster attached for %d job(s) still running",
                run_id,
                len(pending),
            )
            return

        for participant in roster:
            try:
                await self.client.remove_participant(participant)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "debate %s: could not take %s off the forum",
                    run_id,
                    participant.identity,
                    exc_info=True,
                )

            # Mark it retired even when h5i refused.
            #
            # These two are not one operation: the first is an external side
            # effect, the second a row. Doing them under one `try` meant that a
            # participant h5i had *already* revoked — the commonest refusal —
            # left our row saying `active` forever, because the failure skipped
            # the write. The row records "attached according to us"; if h5i no
            # longer has it, the row is simply stale, and every later read of the
            # roster inherits the mistake.
            try:
                await debate_service.deactivate_participant(
                    session, run_id=run_id, identity=participant.identity
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "debate %s: could not record %s as retired",
                    run_id,
                    participant.identity,
                    exc_info=True,
                )


def _deps(
    run: DebateRunTable, round_index: int, participant: Participant | None = None
) -> DebateDeps:
    """
    Fresh deps for one turn.

    Built per turn rather than per debate because `receipts` accumulates during a
    run and is drained onto that turn's post — sharing one object across turns
    would attach round one's citations to round four's post.
    """
    return DebateDeps(
        topic=run.topic,
        framing=run.framing,
        thread_id=run.thread_id,
        round_index=round_index,
        rounds=run.rounds,
        project_id=str(run.project_id),
        participant=participant,
    )
