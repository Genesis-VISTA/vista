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

from pydantic import BaseModel, Field

from ..db.schemas import (
    DebateParticipantTable,
    DebatePostTable,
    DebateRunTable,
    DebateStatus,
)
from ..utils.misc import now_iso
from .h5i_forum import ForumClient, Participant, Thread


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
    rounds: int | _Unset = _UNSET,
    rounds_done: int | _Unset = _UNSET,
    verdict: dict[str, Any] | None | _Unset = _UNSET,
) -> DebateRunTable:
    run = await require_debate(session, run_id)
    if status is not _UNSET:
        run.status = status
    if rounds is not _UNSET:
        run.rounds = rounds
    if rounds_done is not _UNSET:
        run.rounds_done = rounds_done
    if verdict is not _UNSET:
        run.verdict = verdict
    run.updated_at = now_iso()
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


async def set_activity(
    session: AsyncSession, *, run_id: uuid.UUID, activity: str | None
) -> None:
    """
    Record what the debate is doing, or clear it.

    Committed by the caller's checkpoint rather than here: this is written on the
    debate's own session between turns, and a commit inside would expire the
    caller's objects mid-round.
    """
    run = await require_debate(session, run_id)
    run.activity = activity
    run.activity_since = now_iso() if activity else None
    run.updated_at = now_iso()
    session.add(run)
    await session.flush()


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
    granted_tools: list[str] | None = None,
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
        granted_tools=granted_tools or [],
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def record_granted_tools(
    session: AsyncSession, *, run_id: uuid.UUID, granted: dict[str, list[str]]
) -> None:
    """
    Correct the record of what each attached role may use.

    The roster is attached before the debate has any tools. `open_debate` builds
    an orchestrator to create the thread, and at that moment the run does not
    exist yet — so `build_run_grounding`, which needs the run to find the
    project's knowledge bases and the opener's credentials, cannot have been
    called. The roster rows are written from that bare orchestrator and record
    the two tools it has, while the debate then runs with a fully wired one.

    The result was a roster panel that said a debate had `prior_debates` and
    nothing else, on a run whose posts cite literature search, an attached paper
    and a commissioned job. So the row is rewritten by whoever actually runs the
    debate, which is the only party that knows.

    Silent about roles it is not given: a debate continued for extra rounds has
    retired stints on its record, and what *they* were granted is history, not
    something to restate in today's terms.
    """
    for row in await list_active_participants(session, run_id=run_id):
        tools = granted.get(row.debate_role)
        if tools is None or list(row.granted_tools) == tools:
            continue
        row.granted_tools = list(tools)
        session.add(row)
    await session.flush()


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


async def list_active_participants(
    session: AsyncSession, *, run_id: uuid.UUID
) -> list[DebateParticipantTable]:
    """
    Only the roles currently attached to the forum.

    A debate that has been continued has more than one roster on its record —
    the retired stint and the current one — and a revoked identity cannot post.
    Rebuilding the client's roster from every row would hand the orchestrator
    boxes that no longer exist.
    """
    return [
        row for row in await list_participants(session, run_id=run_id) if row.active
    ]


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


class Refresh(BaseModel):
    """What one look at the forum turned up."""

    new_posts: list[str] = Field(default_factory=list)
    """Post ids seen for the first time — ours and peers' alike."""

    closed_remotely: bool = False
    """The forum says closed while our record still said otherwise."""


async def refresh_from_forum(
    session: AsyncSession, client: "ForumClient", run: DebateRunTable
) -> Refresh:
    """
    Re-read a debate's thread and fold anything new into the projection.

    The reason this exists: h5i syncs with the remote on every host-side read, so
    while a debate is arguing its own reads keep it current for free. Between
    rounds, and after it ends, nothing reads — and a peer's comment would sit on
    the remote unseen. Whatever is watching a debate has to do the reading.

    Also reconciles closure. A peer with push access can close a thread they did
    not open (contract §8.2), so the forum's status can move without anything
    here deciding it did.
    """
    thread = await client.read_thread(run.thread_id)
    created = await project_thread(session, run_id=run.id, thread=thread)

    closed_remotely = thread.is_closed and run.status in ACTIVE_STATUSES
    if closed_remotely:
        await set_status(session, run_id=run.id, status="closed")

    return Refresh(
        new_posts=[row.post_id for row in created], closed_remotely=closed_remotely
    )


async def record_author(
    session: AsyncSession, *, run_id: uuid.UUID, post_id: str, authored_by: str
) -> None:
    """
    Name the VISTA account behind a post this deployment made itself.

    Only ever called on the path that created the post, where the request was
    authenticated — never from the projection, which reads a thread anyone may
    have written to. `project_thread` refreshes votes and lane on replay and
    nothing else, so this survives every subsequent re-read.
    """
    row = (
        await session.exec(
            select(DebatePostTable)
            .where(DebatePostTable.run_id == run_id)
            .where(DebatePostTable.post_id == post_id)
        )
    ).first()
    if row is None:  # the confirm-by-reading step should make this unreachable
        return
    row.authored_by = authored_by
    session.add(row)
    await session.flush()


def closed_by(posts: list[DebatePostTable]) -> str | None:
    """
    Who ended this debate: `"operator"`, `"peer"`, or None if it is not closed.

    Derived from the CLOSED post's vouch lane rather than stored, because that is
    where the fact actually lives — and because labelling every closure "ended
    early" would credit the operator with a decision a peer may have made.
    """
    closing = next((p for p in reversed(posts) if p.kind == "CLOSED"), None)
    if closing is None:
        return None
    return "operator" if closing.vouch_lane == "host-observed" else "peer"


async def record_post_tools(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    post_id: str,
    tools: list[dict[str, Any]],
) -> None:
    """
    Attach tool provenance to a post the projection already created.

    Separate from `project_thread` because the forum does not carry it: h5i knows
    what was said, not how the agent arrived at it. That half of the record is
    ours, and it is written here rather than inferred later.
    """
    if not tools:
        return
    row = (
        await session.exec(
            select(DebatePostTable).where(
                DebatePostTable.run_id == run_id,
                DebatePostTable.post_id == post_id,
            )
        )
    ).first()
    if row is None:
        return
    row.tools_used = tools
    session.add(row)
    await session.flush()


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
        observed_votes, peer_votes = (
            thread.tally_split(post.id) if not post.is_vote else (0, 0)
        )
        row = existing.get(post.id)
        if row is not None:
            # Only what can legitimately change after the fact.
            row.votes = observed_votes
            row.peer_votes = peer_votes
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
            votes=observed_votes,
            peer_votes=peer_votes,
            # Spelled out for the same reason as `verdict=None` in create_debate:
            # a JSON `sa_column` does not carry its default into __init__.
            tools_used=[],
            # A round is a unit of the agents' work. The human's interjections
            # and h5i's own bookkeeping happen alongside it, not inside it, so
            # stamping them with a round would credit the debate with words it
            # did not produce.
            # A round is our agents' work. The operator's interjections, h5i's
            # own bookkeeping, and anything a peer pushed over the remote all
            # happen alongside it rather than inside it.
            round_index=(
                round_index
                if (thread.is_observed(post) and not post.claims_human)
                and post.agent_authored
                else None
            ),
        )
        session.add(row)
        created.append(row)

    await session.flush()
    for row in created:
        await session.refresh(row)
    return created
