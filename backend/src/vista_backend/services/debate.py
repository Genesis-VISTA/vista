"""
Debate service: durable state for an agent-forum run.

A pure data layer over the debate tables (see db/schemas.py) — no HTTP, no agent
logic and no subprocess. The orchestrator and the API build on it. Not-found
conditions raise ValueError so the service is usable outside HTTP; the API maps
them to 404s. Mutators touch `updated_at` and flush + refresh, mirroring
`campaign.py`.

The forum's git store is the source of truth for what was said. What lives here
is a projection of it, and `project_thread` is the one function that writes posts:
it replays `forum read --json` into the tables, and is idempotent so replaying a
thread after a reconnect converges instead of duplicating. Nothing else should
insert a post — a post this service invented would be a post nobody said.
"""

import enum
import uuid
from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import (
    DebateParticipantTable,
    DebatePostTable,
    DebateRunTable,
    DebateStatus,
)
from ..utils.misc import now_iso
from .h5i_forum import Participant, Thread


# Statuses a debate can still make progress from. "converged", "closed" and
# "failed" are terminal.
ACTIVE_STATUSES: tuple[DebateStatus, ...] = ("setting_up", "debating")


class _Unset(enum.Enum):
    """Sentinel so update_* can tell "leave unchanged" from "set to None"."""

    UNSET = enum.auto()


_UNSET = _Unset.UNSET


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #


async def create_debate(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    topic: str,
    thread_id: str,
    framing: str | None = None,
    rounds: int = 5,
) -> DebateRunTable:
    """Record a debate whose forum thread already exists."""
    now = now_iso()
    run = DebateRunTable(
        project_id=project_id,
        user_id=user_id,
        topic=topic,
        framing=framing,
        thread_id=thread_id,
        rounds=rounds,
        rounds_done=0,
        status="setting_up",
        # Passed explicitly rather than left to the field default: a JSON
        # `sa_column` does not carry its default into the generated __init__,
        # the same reason campaign.py spells out `result=None`.
        verdict=None,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


async def get_debate(session: AsyncSession, run_id: uuid.UUID) -> DebateRunTable | None:
    return (
        await session.exec(select(DebateRunTable).where(DebateRunTable.id == run_id))
    ).first()


async def require_debate(session: AsyncSession, run_id: uuid.UUID) -> DebateRunTable:
    run = await get_debate(session, run_id)
    if run is None:
        raise ValueError(f"Debate {run_id} not found")
    return run


async def require_debate_in_project(
    session: AsyncSession, *, run_id: uuid.UUID, project_id: uuid.UUID
) -> DebateRunTable:
    """Load a run and confirm it belongs to `project_id` (the API's access boundary)."""
    run = await get_debate(session, run_id)
    if run is None or run.project_id != project_id:
        raise ValueError(f"Debate {run_id} not found in project {project_id}")
    return run


async def get_debate_by_thread(
    session: AsyncSession, thread_id: str
) -> DebateRunTable | None:
    """Find a run by its forum thread — the way back from a forum id to VISTA."""
    return (
        await session.exec(
            select(DebateRunTable).where(DebateRunTable.thread_id == thread_id)
        )
    ).first()


async def list_debates(
    session: AsyncSession,
    *,
    project_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    statuses: tuple[DebateStatus, ...] | None = None,
) -> list[DebateRunTable]:
    stmt = select(DebateRunTable)
    if project_id is not None:
        stmt = stmt.where(DebateRunTable.project_id == project_id)
    if user_id is not None:
        stmt = stmt.where(DebateRunTable.user_id == user_id)
    if statuses is not None:
        stmt = stmt.where(col(DebateRunTable.status).in_(statuses))
    stmt = stmt.order_by(col(DebateRunTable.created_at).desc())
    return list(await session.exec(stmt))


async def update_debate(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    status: DebateStatus | _Unset = _UNSET,
    rounds_done: int | _Unset = _UNSET,
    verdict: dict[str, Any] | None | _Unset = _UNSET,
) -> DebateRunTable:
    run = await require_debate(session, run_id)
    if status is not _UNSET:
        run.status = status
    if rounds_done is not _UNSET:
        run.rounds_done = rounds_done
    if verdict is not _UNSET:
        run.verdict = verdict
    run.updated_at = now_iso()
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


async def set_status(
    session: AsyncSession, *, run_id: uuid.UUID, status: DebateStatus
) -> DebateRunTable:
    return await update_debate(session, run_id=run_id, status=status)


async def record_verdict(
    session: AsyncSession, *, run_id: uuid.UUID, verdict: dict[str, Any]
) -> DebateRunTable:
    """Store the referee's ranked hypothesis and mark the debate converged."""
    return await update_debate(
        session, run_id=run_id, verdict=verdict, status="converged"
    )


# --------------------------------------------------------------------------- #
# Participants
# --------------------------------------------------------------------------- #


async def add_participant(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    participant: Participant,
    debate_role: str,
) -> DebateParticipantTable:
    """
    Record a role that has been attached to the forum.

    Takes the client's `Participant` rather than loose strings so the box id and
    policy digest come from `h5i box status`, not from a caller's assumption
    about how h5i names things.
    """
    row = DebateParticipantTable(
        run_id=run_id,
        identity=participant.identity,
        debate_role=debate_role,
        forum_role=str(participant.role),
        box_slug=participant.box_slug,
        box_id=participant.box_id,
        policy_digest=participant.policy_digest,
        active=True,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def list_participants(
    session: AsyncSession, *, run_id: uuid.UUID
) -> list[DebateParticipantTable]:
    return list(
        await session.exec(
            select(DebateParticipantTable)
            .where(DebateParticipantTable.run_id == run_id)
            .order_by(col(DebateParticipantTable.identity))
        )
    )


async def deactivate_participant(
    session: AsyncSession, *, run_id: uuid.UUID, identity: str
) -> DebateParticipantTable:
    """
    Mark a role revoked.

    Its posts stay and stay attributed: revoking changes who may post next, not
    what was already said.
    """
    row = (
        await session.exec(
            select(DebateParticipantTable).where(
                DebateParticipantTable.run_id == run_id,
                DebateParticipantTable.identity == identity,
            )
        )
    ).first()
    if row is None:
        raise ValueError(f"Participant {identity} not found on debate {run_id}")
    row.active = False
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


# --------------------------------------------------------------------------- #
# Posts
# --------------------------------------------------------------------------- #


async def list_posts(
    session: AsyncSession, *, run_id: uuid.UUID, include_votes: bool = False
) -> list[DebatePostTable]:
    stmt = select(DebatePostTable).where(DebatePostTable.run_id == run_id)
    if not include_votes:
        stmt = stmt.where(col(DebatePostTable.kind).not_in(("UPVOTE", "DOWNVOTE")))
    stmt = stmt.order_by(col(DebatePostTable.ts), col(DebatePostTable.post_id))
    return list(await session.exec(stmt))


async def project_thread(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    thread: Thread,
    round_index: int | None = None,
) -> list[DebatePostTable]:
    """
    Replay a forum thread into the tables, and return the posts that were new.

    Idempotent by `(run_id, post_id)`: a thread replayed twice converges rather
    than duplicating, which is what makes it safe to call on every poll, after a
    reconnect, or to rebuild the projection from scratch.

    Existing rows are refreshed rather than skipped, because a post's *tally*
    moves after it is written — a peer can upvote something from three rounds
    ago. The body never changes; the vote count does.

    `round_index` labels only the posts this call is seeing for the first time,
    so a replay does not relabel history with the round that happened to be
    running when someone re-read the thread.
    """
    existing = {
        row.post_id: row
        for row in await session.exec(
            select(DebatePostTable).where(DebatePostTable.run_id == run_id)
        )
    }

    created: list[DebatePostTable] = []
    for post in thread.posts:
        tally = thread.tally(post.id) if not post.is_vote else 0
        row = existing.get(post.id)
        if row is not None:
            # Only what can legitimately change after the fact.
            row.votes = tally
            row.vouch_lane = thread.lane(post.id)
            session.add(row)
            continue

        row = DebatePostTable(
            run_id=run_id,
            post_id=post.id,
            kind=post.kind,
            body=post.body,
            sender=post.sender,
            forum_role=post.role,
            box_id=post.box_id,
            policy_digest=post.policy_digest,
            origin=post.origin,
            reply_to=post.reply_to,
            ts=post.ts,
            vouch_lane=thread.lane(post.id),
            denied=post.denied,
            votes=tally,
            # A round is a unit of the agents' work. The human's interjections
            # and h5i's own bookkeeping happen alongside it, not inside it, so
            # stamping them with a round would credit the debate with words it
            # did not produce.
            round_index=(
                None if (post.from_human or not post.agent_authored) else round_index
            ),
        )
        session.add(row)
        created.append(row)

    await session.flush()
    for row in created:
        await session.refresh(row)
    return created
