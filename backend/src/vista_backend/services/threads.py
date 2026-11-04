"""
Shared threads: this install's copy of every thread on a project's forum.

`debate.py` keeps what this install's own runs did. This module keeps what the
forum holds, whoever wrote it, so that the Hypothesis Lab can list, show and
join a thread another install opened (openspec/changes/forum-shared-threads).

Everything written here is a copy of git and can be rebuilt by syncing: nothing
here is a fact the forum does not hold, except which threads went missing.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import col, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import (
    DebatePostTable,
    DebateRunTable,
    ForumPostTable,
    ForumThreadTable,
)
from ..utils.misc import now_iso
from .forum_git import (
    ForumClient,
    LiveRun,
    Post,
    PostKind,
    RunSpec,
    Thread,
    ThreadMissing,
    live_run,
)


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


async def get_thread(
    session: AsyncSession, *, project_id: uuid.UUID, thread_id: str
) -> ForumThreadTable | None:
    return (
        await session.exec(
            select(ForumThreadTable).where(
                ForumThreadTable.project_id == project_id,
                ForumThreadTable.thread_id == thread_id,
            )
        )
    ).first()


async def require_thread(
    session: AsyncSession, *, project_id: uuid.UUID, thread_id: str
) -> ForumThreadTable:
    row = await get_thread(session, project_id=project_id, thread_id=thread_id)
    if row is None:
        raise ValueError(f"Thread {thread_id} not found in project {project_id}")
    return row


async def list_threads(
    session: AsyncSession, *, project_id: uuid.UUID
) -> list[ForumThreadTable]:
    """The project's threads, newest first."""
    return list(
        await session.exec(
            select(ForumThreadTable)
            .where(ForumThreadTable.project_id == project_id)
            .order_by(col(ForumThreadTable.opened_at).desc())
        )
    )


async def list_posts(
    session: AsyncSession, *, thread: ForumThreadTable, include_votes: bool = True
) -> list[ForumPostTable]:
    """
    The thread's posts in the order they were projected, which is the order
    the remote accepted them (projection walks the thread in that order).
    """
    stmt = select(ForumPostTable).where(ForumPostTable.forum_thread_id == thread.id)
    if not include_votes:
        stmt = stmt.where(col(ForumPostTable.kind).not_in(("UPVOTE", "DOWNVOTE")))
    return list(await session.exec(stmt.order_by(col(ForumPostTable.seq))))


async def runs_of(
    session: AsyncSession, *, thread: ForumThreadTable
) -> list[DebateRunTable]:
    """This install's runs in the thread, oldest first."""
    return list(
        await session.exec(
            select(DebateRunTable)
            .where(
                or_(
                    DebateRunTable.forum_thread_id == thread.id,
                    (DebateRunTable.project_id == thread.project_id)
                    & (DebateRunTable.thread_id == thread.thread_id),
                )
            )
            .order_by(col(DebateRunTable.created_at))
        )
    )


def as_post(row: ForumPostTable) -> Post:
    """A stored post as the forum client's model, for rules shared with it."""
    return Post(
        id=row.post_id,
        thread="",
        kind=row.kind,
        body=row.body,
        sender=row.sender,
        role=row.forum_role,
        ts=row.ts,
        origin=row.origin,
        reply_to=row.reply_to,
        run=RunSpec.model_validate(row.run) if row.run else None,
        verdict=row.verdict,
    )


def live_run_of(
    rows: list[ForumPostTable], *, expiry: float, now: datetime | None = None
) -> LiveRun | None:
    """The thread's live run, from its stored posts (`forum_git.live_run`)."""
    return live_run(
        [as_post(r) for r in rows], now=now or datetime.now(UTC), expiry=expiry
    )


class CurrentVerdict(BaseModel):
    """The thread's latest DONE post: its structured verdict when it has one."""

    post_id: str
    sender: str
    origin: str | None
    vouch_lane: str | None
    body: str
    verdict: dict[str, Any] | None = None


def current_verdict(rows: list[ForumPostTable]) -> CurrentVerdict | None:
    """The newest DONE post in the thread, whichever install wrote it."""
    done = next((r for r in reversed(rows) if r.kind == PostKind.DONE), None)
    if done is None:
        return None
    return CurrentVerdict(
        post_id=done.post_id,
        sender=done.sender,
        origin=done.origin,
        vouch_lane=done.vouch_lane,
        body=done.body,
        verdict=done.verdict,
    )


class ThreadPost(BaseModel):
    """A post as the lab shows it: the forum's copy, with this install's notes."""

    id: uuid.UUID
    post_id: str
    kind: str
    body: str
    sender: str
    forum_role: str
    origin: str | None = None
    reply_to: str | None = None
    ts: str
    vouch_lane: str | None = None
    published: bool | None = None
    on_remote: bool | None = None
    votes: int = 0
    peer_votes: int = 0
    run: dict[str, Any] | None = None
    verdict: dict[str, Any] | None = None
    run_id: uuid.UUID | None = None
    """ The local run that wrote it, when this install's agents did. """
    round_index: int | None = None
    tools_used: list[dict[str, Any]] = Field(default_factory=list)
    model: str | None = None
    authored_by: str | None = None
    denied: str | None = None


async def posts_with_notes(
    session: AsyncSession, *, thread: ForumThreadTable, include_votes: bool = False
) -> list[ThreadPost]:
    """
    The thread's posts, each with the notes this install's runs kept on it.

    A post can sit in several runs' `debate_post` rows (each run copies the
    whole thread it read), but only the run that wrote it has notes on it, so
    the first row with notes wins.
    """
    rows = await list_posts(session, thread=thread, include_votes=include_votes)
    runs = [r.id for r in await runs_of(session, thread=thread)]
    notes: dict[str, DebatePostTable] = {}
    if runs:
        for note in await session.exec(
            select(DebatePostTable).where(
                col(DebatePostTable.run_id).in_(runs),
                col(DebatePostTable.post_id).in_([r.post_id for r in rows]),
            )
        ):
            have = notes.get(note.post_id)
            if have is None or _has_notes(note) and not _has_notes(have):
                notes[note.post_id] = note
    out = []
    for row in rows:
        note = notes.get(row.post_id)
        out.append(
            ThreadPost(
                **row.model_dump(exclude={"forum_thread_id", "seq"}),
                run_id=note.run_id if note is not None and _has_notes(note) else None,
                round_index=note.round_index if note else None,
                tools_used=list(note.tools_used or []) if note else [],
                model=note.model if note else None,
                authored_by=note.authored_by if note else None,
                denied=note.denied if note else None,
            )
        )
    return out


def _has_notes(row: DebatePostTable) -> bool:
    return bool(
        row.round_index is not None or row.tools_used or row.model or row.authored_by
    )


# --------------------------------------------------------------------------- #
# Writing: projecting git into the tables
# --------------------------------------------------------------------------- #


async def ensure_thread(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    thread_id: str,
    title: str,
    framing: str | None = None,
    created_by: str = "",
    opened_at: str = "",
) -> ForumThreadTable:
    """The thread's row, created if this install has none. Never overwrites."""
    now = now_iso()
    await session.exec(
        sqlite_insert(ForumThreadTable)
        .values(
            id=uuid.uuid4(),
            project_id=project_id,
            thread_id=thread_id,
            title=title,
            framing=framing,
            created_by=created_by,
            opened_at=opened_at or now,
            status="open",
            verdict=None,
            thread_missing=False,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["project_id", "thread_id"])
    )
    return await require_thread(session, project_id=project_id, thread_id=thread_id)


async def project_forum_thread(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    thread: Thread,
    tip: str | None = None,
) -> ForumThreadTable:
    """
    Copy a read thread into `forum_thread` and `forum_post`. Idempotent.

    Like `debate.project_thread`: posts already stored have only what may
    change after the fact refreshed (tallies, lane, publication), a post the
    thread no longer holds is kept and marked `on_remote = False`, and a post
    another writer stored first is skipped rather than an error.
    """
    task = next((p for p in thread.posts if p.kind == PostKind.TASK), None)
    row = await ensure_thread(
        session,
        project_id=project_id,
        thread_id=thread.id,
        title=thread.header.title,
        framing=task.body if task else None,
        created_by=thread.header.created_by,
        opened_at=thread.header.created_at,
    )
    row.title = thread.header.title
    row.created_by = thread.header.created_by
    if thread.header.created_at:
        row.opened_at = thread.header.created_at
    if task is not None:
        row.framing = task.body
    row.status = thread.status
    row.thread_missing = False
    if tip is not None:
        row.tip = tip
    row.synced_at = row.updated_at = now_iso()

    existing = {
        r.post_id: r
        for r in await session.exec(
            select(ForumPostTable).where(ForumPostTable.forum_thread_id == row.id)
        )
    }
    seq = max((r.seq for r in existing.values()), default=0)
    fresh: list[dict[str, Any]] = []
    for post in thread.posts:
        observed, peer = thread.tally_split(post.id) if not post.is_vote else (0, 0)
        published = thread.published.get(post.id)
        stored = existing.get(post.id)
        if stored is not None:
            stored.votes, stored.peer_votes = observed, peer
            stored.vouch_lane = thread.lane(post.id)
            stored.published = published
            stored.on_remote = None if published is False else True
            session.add(stored)
            continue
        seq += 1
        fresh.append(
            {
                "id": uuid.uuid4(),
                "forum_thread_id": row.id,
                "seq": seq,
                "post_id": post.id,
                "kind": post.kind,
                "body": post.body,
                "sender": post.sender,
                "forum_role": post.role,
                "origin": post.origin,
                "reply_to": post.reply_to,
                "ts": post.ts,
                "vouch_lane": thread.lane(post.id),
                "published": published,
                "on_remote": None if published is False else True,
                "votes": observed,
                "peer_votes": peer,
                "run": post.run.model_dump() if post.run else None,
                "verdict": post.verdict,
            }
        )
    present = {p.id for p in thread.posts}
    for post_id, stored in existing.items():
        if post_id not in present and stored.on_remote is not False:
            stored.on_remote = False
            session.add(stored)
    for values in fresh:
        await session.exec(
            sqlite_insert(ForumPostTable)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["forum_thread_id", "post_id"])
        )
    await session.flush()

    rows = await list_posts(session, thread=row)
    latest = current_verdict(rows)
    row.verdict = latest.verdict if latest else None
    session.add(row)
    await session.flush()
    return row


async def mark_thread_missing(
    session: AsyncSession, *, thread: ForumThreadTable
) -> None:
    if thread.thread_missing:
        return
    thread.thread_missing = True
    thread.updated_at = now_iso()
    session.add(thread)
    await session.flush()


async def link_run(
    session: AsyncSession, *, run: DebateRunTable, thread: ForumThreadTable
) -> None:
    if run.forum_thread_id == thread.id:
        return
    run.forum_thread_id = thread.id
    session.add(run)
    await session.flush()


# --------------------------------------------------------------------------- #
# Syncing a whole forum
# --------------------------------------------------------------------------- #


class SyncOutcome(BaseModel):
    reached: bool
    """ Whether the fetch reached the remote. Not reaching it is not an error. """
    projected: list[str] = Field(default_factory=list)
    """ Threads whose copy was refreshed. """
    missing: list[str] = Field(default_factory=list)


async def carry_over(
    session: AsyncSession, *, project_id: uuid.UUID, host_id: str
) -> None:
    """
    Give runs from before shared threads their thread row (design D10).

    Those runs were all opened by this install, which is the only place a run
    could exist then. A thread that has gone missing keeps its posts: copied
    from the run's own rows, since git no longer has them.
    """
    orphans = list(
        await session.exec(
            select(DebateRunTable).where(
                DebateRunTable.project_id == project_id,
                col(DebateRunTable.forum_thread_id).is_(None),
            )
        )
    )
    for run in orphans:
        thread = await ensure_thread(
            session,
            project_id=project_id,
            thread_id=run.thread_id,
            title=run.topic,
            framing=run.framing,
            created_by=host_id,
            opened_at=run.created_at,
        )
        await link_run(session, run=run, thread=thread)
        if run.thread_missing:
            await _copy_run_posts(session, run=run, thread=thread)
            thread.verdict = run.verdict
            thread.status = "closed" if run.status == "closed" else thread.status
            await mark_thread_missing(session, thread=thread)


async def _copy_run_posts(
    session: AsyncSession, *, run: DebateRunTable, thread: ForumThreadTable
) -> None:
    stored = list(
        await session.exec(
            select(DebatePostTable)
            .where(DebatePostTable.run_id == run.id)
            .order_by(col(DebatePostTable.ts), col(DebatePostTable.post_id))
        )
    )
    for seq, post in enumerate(stored, start=1):
        await session.exec(
            sqlite_insert(ForumPostTable)
            .values(
                id=uuid.uuid4(),
                forum_thread_id=thread.id,
                seq=seq,
                post_id=post.post_id,
                kind=post.kind,
                body=post.body,
                sender=post.sender,
                forum_role=post.forum_role,
                origin=post.origin,
                reply_to=post.reply_to,
                ts=post.ts,
                vouch_lane=post.vouch_lane,
                published=post.published,
                on_remote=False,
                votes=post.votes,
                peer_votes=post.peer_votes,
                run=None,
                verdict=None,
            )
            .on_conflict_do_nothing(index_elements=["forum_thread_id", "post_id"])
        )
    await session.flush()


async def sync_forum(
    session: AsyncSession, *, project_id: uuid.UUID, client: ForumClient
) -> SyncOutcome:
    """
    Bring every thread on the project's forum into the tables (design D3).

    One fetch, then only threads whose tip moved are read. A thread this
    install holds that a successful fetch no longer lists is marked missing.
    Never publishes; a failed fetch reads what is here.
    """
    await carry_over(session, project_id=project_id, host_id=client.host_id)
    reached, tips = await client.thread_tips()
    known = {t.thread_id: t for t in await list_threads(session, project_id=project_id)}
    outcome = SyncOutcome(reached=reached)
    for thread_id, tip in tips.items():
        row = known.get(thread_id)
        if row is not None and row.tip == tip and not row.thread_missing:
            continue
        try:
            thread = await client.read_thread(thread_id)
        except ThreadMissing:
            continue
        await project_forum_thread(
            session, project_id=project_id, thread=thread, tip=tip
        )
        outcome.projected.append(thread_id)
    if reached:
        for thread_id, row in known.items():
            if thread_id not in tips and not row.thread_missing:
                await mark_thread_missing(session, thread=row)
                outcome.missing.append(thread_id)
    return outcome
