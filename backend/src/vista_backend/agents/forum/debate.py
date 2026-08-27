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
from typing import Awaitable, Callable

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


logger = logging.getLogger(__name__)


# The debate's roster. The scientific role is the forum *identity*, which the host
# stamps; h5i's own role vocabulary is only worker/reviewer/observer, so the
# mapping is lossy in that direction and the identity carries the real meaning.
ROSTER: dict[DebateRole, ParticipantRole] = {
    "proposer": ParticipantRole.WORKER,
    "reviewer": ParticipantRole.REVIEWER,
    "referee": ParticipantRole.WORKER,
}


PostHook = Callable[[Post], Awaitable[None]]
"""Called as each post lands. The API's change feed hangs off this."""


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
    ) -> None:
        self.client = client
        self.roles = roles or RoleAgents()
        self.on_post = on_post

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
                session, run_id=run.id, participant=participant, debate_role=role
            )

        return await debate_service.set_status(
            session, run_id=run.id, status="debating"
        )

    async def _participants(
        self, session: AsyncSession, run: DebateRunTable
    ) -> dict[DebateRole, Participant]:
        """Rebuild the client's view of the roster from what was recorded."""
        rows = await debate_service.list_participants(session, run_id=run.id)
        roster: dict[DebateRole, Participant] = {}
        for row in rows:
            roster[row.debate_role] = Participant(  # type: ignore[index]
                identity=row.identity,
                role=ParticipantRole(row.forum_role),
                box_slug=row.box_slug,
                box_id=row.box_id,
                policy_digest=row.policy_digest,
            )
        return roster

    # -- the loop ---------------------------------------------------------- #

    async def run(self, session: AsyncSession, run: DebateRunTable) -> DebateRunTable:
        """
        Argue to a verdict, or until the human stops it.

        `ThreadClosed` is caught here rather than inside a round because closure
        can land between any two calls — the human is not waiting for a round
        boundary — and every one of those points means the same thing.
        """
        roster = await self._participants(session, run)
        try:
            while run.rounds_done < run.rounds:
                run = await self.run_round(session, run, roster)
            run = await self.conclude(session, run, roster)
        except ThreadClosed:
            logger.info("debate %s: the human closed the thread", run.id)
            run = await self._sync(session, run)
            run = await debate_service.set_status(
                session, run_id=run.id, status="closed"
            )
        finally:
            await self._retire(session, run, roster)
        return run

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
        index = run.rounds_done

        propose_deps = _deps(run, index, roster["proposer"])
        thread = await self._sync_thread(session, run, round_index=index)
        hypothesis = await self.roles.propose(propose_deps, thread)
        proposal = await self._post(
            session,
            run,
            roster["proposer"],
            hypothesis.to_post_body(),
            kind=PostKind.PROPOSAL,
            round_index=index,
            deps=propose_deps,
        )

        review_deps = _deps(run, index, roster["reviewer"])
        thread = await self._sync_thread(session, run, round_index=index)
        critique = await self.roles.review(review_deps, thread)
        if critique.stance == "concede":
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

        return await debate_service.update_debate(
            session, run_id=run.id, rounds_done=index + 1
        )

    async def conclude(
        self,
        session: AsyncSession,
        run: DebateRunTable,
        roster: dict[DebateRole, Participant],
    ) -> DebateRunTable:
        """The Referee rules on what the thread produced."""
        thread = await self._sync_thread(session, run, round_index=None)
        deps = _deps(run, run.rounds_done, roster["referee"])
        verdict = await self.roles.rule(deps, thread)
        await self._post(
            session,
            run,
            roster["referee"],
            verdict.to_post_body(),
            kind=PostKind.DONE,
            round_index=None,
            deps=deps,
        )
        return await debate_service.record_verdict(
            session, run_id=run.id, verdict=verdict.model_dump()
        )

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
        if self.on_post is not None:
            await self.on_post(post)
        return post

    async def _stage_receipts(
        self, participant: Participant, deps: DebateDeps | None
    ) -> str | None:
        """
        Turn this turn's fetch receipts into an attachment on the post.

        h5i takes one attachment per post, so several fetches become one file.
        A refused fetch has a receipt too, and it is kept for the same reason a
        successful one is: what the debate could not reach is part of the record.
        """
        if deps is None or not deps.receipts:
            return None
        body = "\n\n".join(receipt for _, receipt in deps.receipts)
        try:
            return await self.client.stage_attachment(
                participant, "citations.json", body
            )
        except Exception:  # noqa: BLE001 — a citation must never lose the post
            logger.warning("debate: could not stage receipts", exc_info=True)
            return None

    async def _sync_thread(
        self, session: AsyncSession, run: DebateRunTable, *, round_index: int | None
    ) -> Thread:
        """Read the forum and project it. The read is what makes the DB catch up."""
        thread = await self.client.read_thread(run.thread_id)
        await debate_service.project_thread(
            session, run_id=run.id, thread=thread, round_index=round_index
        )
        return thread

    async def _sync(self, session: AsyncSession, run: DebateRunTable) -> DebateRunTable:
        """
        Project whatever the thread ended up holding.

        Called on the closed path so the record includes the human's closing
        posts and h5i's own CLOSED marker, rather than stopping at whatever the
        agents last managed to say.
        """
        try:
            await self._sync_thread(session, run, round_index=None)
        except Exception:  # noqa: BLE001 — a failed final read must not mask the close
            logger.warning("debate %s: could not read the closed thread", run.id)
        return run

    async def _retire(
        self,
        session: AsyncSession,
        run: DebateRunTable,
        roster: dict[DebateRole, Participant],
    ) -> None:
        """
        Take the roster off the forum and delete its boxes.

        Their posts stay and stay attributed — revoking changes who may post
        next, not what was said. Best effort, and never allowed to fail a debate
        that has already reached its conclusion.
        """
        for role, participant in roster.items():
            try:
                await self.client.remove_participant(participant)
                await debate_service.deactivate_participant(
                    session, run_id=run.id, identity=participant.identity
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "debate %s: could not retire %s", run.id, role, exc_info=True
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
        round_index=round_index,
        rounds=run.rounds,
        project_id=str(run.project_id),
        participant=participant,
    )
