"""
Hypothesis Lab API — list a project's threads, open one, watch it, join it.

Scoped under a project (`project_service.get_project_by_name` 404s an unknown
one), and keyed by forum thread rather than by this install's run
(openspec/changes/forum-shared-threads, design D8): a thread may have been
opened by another install, and may hold runs from several. Routes stay thin:
what the forum holds is copied by `services/threads.py`, this install's runs are
`services/debate.py`, and the argument is in `agents/forum/`.

Opening a thread or continuing one returns as soon as the run exists. The
argument then runs in the background for minutes, and the client follows it on
the event stream.
"""

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncIterator, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from sse_starlette.sse import EventSourceResponse
from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.forum import simulation
from ..agents.forum.debate import ROSTER
from ..agents.forum.project_forum import build_client_for, forum_url_of, lab_enabled
from ..agents.forum.roles import Verdict
from ..agents.forum.wiring import continue_debate_task, models_for, run_debate_task
from ..agents.inference import require_inference_credential
from ..config import settings
from ..db.db import SessionDep, get_engine
from ..db.schemas import (
    DebateCreate,
    DebateParticipantPublic,
    DebateRoleName,
    DebateRunPublic,
    DebateRunTable,
    ForumThreadTable,
    clean_role_models,
)
from ..services import debate as debate_service, local_settings
from ..services import project as project_service
from ..services import threads as thread_service
from ..services.forum_git import (
    POSTABLE_KINDS,
    ForumClient,
    ForumDisabled,
    PostKind,
    ThreadClosed,
    ThreadMissing,
)
from ..services.git_check import git_status
from ..services.local_settings import LocalSettingsDep
from ..services.threads import ThreadPost


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["debates"])


# --------------------------------------------------------------------------- #
# Forum status
# --------------------------------------------------------------------------- #


class PublishError(BaseModel):
    """Why posts did not reach the forum, for the lab page to explain (design D7)."""

    reason: Literal["refused", "unreachable", "failed"]
    """`refused`: the credential was turned away. `unreachable`: the forge was not reached."""
    credential: Literal["settings-github", "settings-gitlab", "system-git"]
    """Which credential was sent, so the page can say which one to fix."""
    message: str
    """Git's own message; it never holds the token, which git gets from the environment."""
    at: str


class ForumStatus(BaseModel):
    """Whether this project's lab can run, and whether its forum is shared."""

    enabled: bool
    """The lab is usable: the forum is on, the project has a URL, and git works."""

    shared: bool
    """Publishing to the project's remote — i.e. outside participants can reach it."""

    remote: str | None = None
    git_ok: bool
    git_reason: str | None = None
    """Why git is unusable, in words for the page. None when `git_ok`."""

    unpublished: int = 0
    """This install's posts the remote does not have yet — e.g. written offline."""

    publish_error: PublishError | None = None
    """Why publishing last failed, or None. Cleared by the next successful push."""


@router.get("/{project_name}/forum/status")
async def forum_status(project_name: str, session: SessionDep) -> ForumStatus:
    """
    This project's forum state, for the page to explain itself with.

    Never raises on a forum that is off or unreachable: this endpoint exists to
    report bad states, so failing on one would defeat it.
    """
    project = await project_service.get_project_by_name(session, project_name)
    git = git_status()
    if not lab_enabled(project):
        return ForumStatus(
            enabled=False, shared=False, git_ok=git.ok, git_reason=git.reason
        )

    client = await _client(session, project)
    url = forum_url_of(project)
    try:
        # The repository's own remote, not the project row's intention: they
        # disagree exactly when it matters, e.g. a save that could not set it.
        remote = await client.remote()
        unpublished = await client.unpublished()
        error = await client.publish_error()
    except Exception:  # noqa: BLE001
        logger.warning("forum: could not read forum status", exc_info=True)
        return ForumStatus(enabled=True, shared=False, git_ok=True)

    shared = remote is not None and remote == url
    return ForumStatus(
        enabled=True,
        shared=shared,
        remote=remote if shared else None,
        git_ok=True,
        unpublished=unpublished,
        publish_error=_publish_error(error),
    )


def _publish_error(record: dict | None) -> PublishError | None:
    """The stored failure, or None for none or one this version cannot read."""
    if not record:
        return None
    try:
        return PublishError.model_validate(record)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# What the lab is sent
# --------------------------------------------------------------------------- #


class LiveRunPublic(BaseModel):
    """A run going on in the thread right now, worked out from the thread."""

    post_id: str
    ours: bool
    """ Started by this install. Otherwise the origin is a peer's claim. """
    origin: str | None
    rounds: int
    posts: int
    started_at: str
    last_post_at: str


class VerdictPublic(BaseModel):
    """The thread's current verdict: its latest DONE post, whoever wrote it."""

    post_id: str
    ours: bool
    """ Written by this install's Referee. """
    sender: str
    body: str
    """ The verdict as the Referee wrote it out: always there. """
    verdict: dict[str, Any] | None = None
    """ The structured verdict, when the post carried a valid one. """


class ThreadPublic(BaseModel):
    """One thread on the project's forum, as the lab's list shows it."""

    id: uuid.UUID
    thread_id: str
    title: str
    framing: str | None = None
    created_by: str
    opened_here: bool
    """ This install opened it, so this install may close it. """
    opened_at: str
    status: str
    """ The forum's status: open, done, blocked or closed. """
    thread_missing: bool
    current_verdict: VerdictPublic | None = None
    live_run: LiveRunPublic | None = None
    run: DebateRunPublic | None = None
    """ This install's run in the thread, if it has argued in it. """


class ThreadStatePublic(BaseModel):
    """A thread, its posts, and this install's part in it."""

    thread: ThreadPublic
    participants: list[DebateParticipantPublic]
    """ This install's roster in the thread; empty when it has not argued here. """
    posts: list[ThreadPost]
    simulations: list[dict[str, Any]] = []


def _verdict_public(
    current: thread_service.CurrentVerdict | None,
) -> VerdictPublic | None:
    if current is None:
        return None
    structured = None
    if current.verdict is not None:
        try:
            structured = Verdict.model_validate(current.verdict).model_dump()
        except ValueError:
            structured = None
    return VerdictPublic(
        post_id=current.post_id,
        ours=current.vouch_lane == "host-observed",
        sender=current.sender,
        body=current.body,
        verdict=structured,
    )


async def _thread_public(
    session: AsyncSession, row: ForumThreadTable, *, host_id: str
) -> ThreadPublic:
    posts = await thread_service.list_posts(session, thread=row)
    live = thread_service.live_run_of(posts, expiry=settings.forum.run_expiry_seconds)
    runs = await thread_service.runs_of(session, thread=row)
    return ThreadPublic(
        id=row.id,
        thread_id=row.thread_id,
        title=row.title,
        framing=row.framing,
        created_by=row.created_by,
        opened_here=row.created_by == host_id,
        opened_at=row.opened_at,
        status=row.status,
        thread_missing=row.thread_missing,
        current_verdict=_verdict_public(thread_service.current_verdict(posts)),
        live_run=LiveRunPublic(
            post_id=live.post_id,
            ours=live.origin == host_id,
            origin=live.origin,
            rounds=live.rounds,
            posts=live.posts,
            started_at=live.started_at,
            last_post_at=live.last_post_at,
        )
        if live is not None
        else None,
        run=DebateRunPublic.model_validate(runs[-1]) if runs else None,
    )


async def _state(
    session: AsyncSession, row: ForumThreadTable, *, host_id: str
) -> ThreadStatePublic:
    runs = await thread_service.runs_of(session, thread=row)
    participants = (
        await debate_service.list_participants(session, run_id=runs[-1].id)
        if runs
        else []
    )
    simulations: list[dict[str, Any]] = []
    for run in runs:
        simulations += [
            record.model_dump()
            for record in await simulation.commissioned_runs(
                session, debate_run_id=run.id
            )
        ]
    return ThreadStatePublic(
        thread=await _thread_public(session, row, host_id=host_id),
        participants=[DebateParticipantPublic.model_validate(p) for p in participants],
        posts=await thread_service.posts_with_notes(session, thread=row),
        simulations=simulations,
    )


# --------------------------------------------------------------------------- #
# Syncing with the forum
# --------------------------------------------------------------------------- #


def stream_session_factory() -> AsyncSession:
    """
    The session the event stream reads through.

    A module-level factory rather than the request's session, because the
    generator outlives the handler that created it, and so tests can point it
    elsewhere. Same shape the campaign monitor uses.
    """
    return AsyncSession(get_engine())


_BACKGROUND: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    """
    Run a debate in the background, holding a reference to its task.

    asyncio keeps only a weak reference to a running task, so a task nobody
    holds can be garbage-collected mid-run.
    """
    task = asyncio.create_task(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task


FORUM_LIST_SECONDS = 30.0
"""
How often the lab's list re-reads the whole forum (design D3). One fetch of
every thread, so slower than a watched thread's refresh.
"""

FORUM_REFRESH_SECONDS = 10.0
"""
How often a watched thread is re-read from the forum. What makes a peer's post
appear; ten seconds is fast enough for a conversation.
"""

_last_refresh: dict[str, float] = {}
_refresh_locks: dict[str, asyncio.Lock] = {}


async def _throttled(key: str, interval: float, fn) -> bool:
    """
    Run `fn` at most once per `interval` across all callers, and never twice at
    once. Returns whether it ran: it commits or rolls back, which expires every
    object the session holds, so the caller re-reads what it needs.
    """
    lock = _refresh_locks.setdefault(key, asyncio.Lock())
    if lock.locked():
        return False
    async with lock:
        now = asyncio.get_running_loop().time()
        if now - _last_refresh.get(key, 0.0) < interval:
            return False
        _last_refresh[key] = now
        await fn()
        return True


async def _sync_list(session: AsyncSession, project_id: uuid.UUID, client) -> None:
    async def sync() -> None:
        try:
            await thread_service.sync_forum(
                session, project_id=project_id, client=client
            )
            await session.commit()
        except Exception:  # noqa: BLE001 — the list survives a failed sync
            logger.warning("forum: could not sync the thread list", exc_info=True)
            await session.rollback()

    await _throttled(f"list:{project_id}", FORUM_LIST_SECONDS, sync)


async def refresh_thread(
    session: AsyncSession, row: ForumThreadTable, client: ForumClient
) -> None:
    """
    Re-read one thread and fold it into the tables: the shared copy, and each
    of this install's runs in it (whose status a peer's close can end).
    Publishes our own posts an earlier push could not.
    """
    if row.thread_missing:
        return
    try:
        thread = await client.read_thread(row.thread_id)
    except ThreadMissing:
        await thread_service.mark_thread_missing(session, thread=row)
        for run in await thread_service.runs_of(session, thread=row):
            await debate_service.mark_thread_missing(session, run_id=run.id)
        return
    if any(not ok for ok in thread.published.values()):
        try:
            await client.publish(row.thread_id)
            thread = await client.read_thread(row.thread_id)
        except Exception:  # noqa: BLE001 — unpublished posts publish next time
            pass
    await thread_service.project_forum_thread(
        session, project_id=row.project_id, thread=thread
    )
    for run in await thread_service.runs_of(session, thread=row):
        await debate_service.project_thread(session, run_id=run.id, thread=thread)
        if thread.is_closed and run.status in debate_service.ACTIVE_STATUSES:
            await debate_service.set_status(session, run_id=run.id, status="closed")


async def _maybe_refresh(
    session: AsyncSession, project_id: uuid.UUID, thread_id: str, client
) -> bool:
    async def refresh() -> None:
        try:
            row = await thread_service.require_thread(
                session, project_id=project_id, thread_id=thread_id
            )
            await refresh_thread(session, row, client)
            await session.commit()
        except Exception:  # noqa: BLE001 — a stream must survive a bad fetch
            logger.warning("thread %s: forum refresh failed", thread_id, exc_info=True)
            await session.rollback()

    return await _throttled(f"thread:{thread_id}", FORUM_REFRESH_SECONDS, refresh)


async def _client(session: AsyncSession, project) -> ForumClient:
    """This project's forum client, sending the token the forum should use."""
    return build_client_for(project, await local_settings.get(session))


STREAM_POLL_SECONDS = 1.0
""" How often the event stream looks for new posts in the tables. """


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


class HumanPost(BaseModel):
    """What the researcher says into a thread."""

    body: str
    kind: PostKind = PostKind.ASK

    @field_validator("kind")
    @classmethod
    def _postable(cls, kind: PostKind) -> PostKind:
        """`TASK`, `CLOSED`, `RUN` and the votes are written by their own verbs."""
        if kind not in POSTABLE_KINDS:
            raise ValueError(
                f"{kind} cannot be posted; use one of "
                f"{', '.join(sorted(str(k) for k in POSTABLE_KINDS))}"
            )
        return kind


class ContinueDebate(BaseModel):
    """How much more arguing to buy, and on which models."""

    rounds: int = Field(default=3, ge=1, le=10)
    models: dict[DebateRoleName, str | None] = Field(default_factory=dict)
    """ Roles to move to another model; null returns one to the default. Roles not named keep theirs. """

    _clean_models = field_validator("models")(clean_role_models)


THREAD_MISSING = "This thread is no longer on the forum."
THREAD_CLOSED = "This thread is closed; it accepts no further posts."


async def _lab(session: AsyncSession, project_name: str):
    """The project and its forum client, or 503 when it has no lab."""
    project = await project_service.get_project_by_name(session, project_name)
    # The client first: when git is missing it says so, which is the reason a
    # researcher can act on, rather than "has no lab".
    try:
        client = await _client(session, project)
    except ForumDisabled as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if not lab_enabled(project):
        raise HTTPException(
            status_code=503, detail=f"Project {project.name!r} has no Hypothesis Lab."
        )
    return project, client


async def _require(
    session: AsyncSession, project_id: uuid.UUID, thread_id: str
) -> ForumThreadTable:
    try:
        return await thread_service.require_thread(
            session, project_id=project_id, thread_id=thread_id
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Thread not found")


@router.get("/{project_name}/threads")
async def list_threads(project_name: str, session: SessionDep) -> list[ThreadPublic]:
    """
    Every thread on the project's forum, newest first, whoever opened it.

    Fetches the forum at most every 30 seconds per project; the lab polls this
    while it is open, and nothing fetches while no lab is open (design D3).
    """
    project = await project_service.get_project_by_name(session, project_name)
    project_id = project.id
    if not lab_enabled(project):
        return []
    client = await _client(session, project)
    await _sync_list(session, project_id, client)
    return [
        await _thread_public(session, row, host_id=client.host_id)
        for row in await thread_service.list_threads(session, project_id=project_id)
    ]


@router.post("/{project_name}/threads")
async def open_thread(
    project_name: str, body: DebateCreate, session: SessionDep, local: LocalSettingsDep
) -> ThreadPublic:
    """Open a thread, attach this install's roster, and start arguing."""
    project, client = await _lab(session, project_name)
    for role in ROSTER:
        require_inference_credential(local, body.models.get(role))

    from ..agents.forum.wiring import build_orchestrator

    try:
        run = await build_orchestrator(client, local=local, models=body.models).start(
            session,
            project_id=project.id,
            topic=body.topic,
            framing=body.framing,
            rounds=body.rounds,
        )
    except ForumDisabled as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    run_id, project_id, thread_id = run.id, run.project_id, run.thread_id
    row = await _require(session, project_id, thread_id)
    payload = await _thread_public(session, row, host_id=client.host_id)
    # Commit before handing the run to a task with its own session.
    await session.commit()
    _spawn(run_debate_task(run_id))
    return payload


@router.get("/{project_name}/threads/{thread_id}")
async def get_thread(
    project_name: str, thread_id: str, session: SessionDep
) -> ThreadStatePublic:
    project = await project_service.get_project_by_name(session, project_name)
    project_id = project.id  # a refresh commits, which expires `project`
    client = await _client(session, project) if lab_enabled(project) else None
    await _require(session, project_id, thread_id)
    if client is not None:
        await _maybe_refresh(session, project_id, thread_id, client)
    row = await _require(session, project_id, thread_id)
    host_id = client.host_id if client is not None else ""
    return await _state(session, row, host_id=host_id)


@router.post("/{project_name}/threads/{thread_id}/posts")
async def post_to_thread(
    project_name: str,
    thread_id: str,
    body: HumanPost,
    session: SessionDep,
    local: LocalSettingsDep,
) -> ThreadPost:
    """
    Say something into a thread, as this install's researcher: any thread that
    is not closed, whoever opened it and whoever's run is live. It starts no
    agent turn; a live run's agents read it at their next turn.
    """
    project, client = await _lab(session, project_name)
    row = await _require(session, project.id, thread_id)
    if row.thread_missing:
        raise HTTPException(status_code=409, detail=THREAD_MISSING)
    try:
        post = await client.post_as_human(thread_id, body.body, kind=body.kind)
    except ThreadClosed:
        raise HTTPException(status_code=409, detail="This thread is closed.")
    except ThreadMissing:
        await thread_service.mark_thread_missing(session, thread=row)
        await session.commit()
        raise HTTPException(status_code=409, detail=THREAD_MISSING)

    await refresh_thread(session, row, client)
    # We know who this was: the researcher at this installation, on the one path
    # where that is knowledge rather than a claim. Never the email.
    if local.display_name:
        await thread_service.record_author(
            session, thread=row, post_id=post.id, authored_by=local.display_name
        )
        for run in await thread_service.runs_of(session, thread=row):
            await debate_service.record_author(
                session, run_id=run.id, post_id=post.id, authored_by=local.display_name
            )
    shown = next(
        (
            p
            for p in await thread_service.posts_with_notes(session, thread=row)
            if p.post_id == post.id
        ),
        None,
    )
    if shown is None:
        raise HTTPException(status_code=500, detail="The post did not reach the thread")
    await session.commit()
    return shown


@router.post("/{project_name}/threads/{thread_id}/runs")
async def continue_thread(
    project_name: str,
    thread_id: str,
    body: ContinueDebate,
    session: SessionDep,
    local: LocalSettingsDep,
) -> ThreadPublic:
    """
    Argue a thread for a few more rounds with this install's own agents.

    Any thread that is not closed, whoever opened it. Refused while a run is
    live in it, this install's or anyone's (one run at a time). A thread this
    install has not argued in is adopted first: it gets a run and roster of
    this install's own.
    """
    project, client = await _lab(session, project_name)
    project_id = project.id
    row = await _require(session, project_id, thread_id)
    if row.thread_missing:
        raise HTTPException(status_code=409, detail=THREAD_MISSING)
    # Read the thread now, not from the last poll: the check below is only as
    # fresh as this read.
    await refresh_thread(session, row, client)
    row = await _require(session, project_id, thread_id)
    if row.thread_missing:
        await session.commit()
        raise HTTPException(status_code=409, detail=THREAD_MISSING)
    if row.status == "closed":
        raise HTTPException(status_code=409, detail=THREAD_CLOSED)

    runs = await thread_service.runs_of(session, thread=row)
    ours: DebateRunTable | None = runs[-1] if runs else None
    if ours is not None and ours.status in debate_service.ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="This debate is still arguing.")
    live = thread_service.live_run_of(
        await thread_service.list_posts(session, thread=row),
        expiry=settings.forum.run_expiry_seconds,
    )
    if live is not None:
        who = (
            "This install's earlier run"
            if live.origin == client.host_id
            else "Another user"
        )
        raise HTTPException(status_code=409, detail=f"{who} is continuing this debate.")

    from ..agents.forum.wiring import build_orchestrator

    if ours is None:
        ours = await build_orchestrator(client, local=local, models=body.models).adopt(
            session, thread=row
        )
    elif body.models:
        changes: dict[str, str | None] = {str(r): m for r, m in body.models.items()}
        await debate_service.set_participant_models(
            session, run_id=ours.id, models=changes
        )
    chosen = await models_for(session, ours)
    for role in ROSTER:
        require_inference_credential(local, chosen.get(role))

    run_id = ours.id
    payload = await _thread_public(session, row, host_id=client.host_id)
    await session.commit()
    _spawn(continue_debate_task(run_id, body.rounds))
    return payload


@router.post("/{project_name}/threads/{thread_id}/close")
async def close_thread(
    project_name: str, thread_id: str, session: SessionDep
) -> ThreadPublic:
    """
    End a thread early: write its CLOSED post. Only the install that opened
    the thread may (`created_by` is its host id); anyone else gets 403.
    """
    project, client = await _lab(session, project_name)
    row = await _require(session, project.id, thread_id)
    if row.created_by != client.host_id:
        raise HTTPException(
            status_code=403,
            detail="Only the researcher who opened a thread can close it.",
        )
    if row.thread_missing:
        raise HTTPException(status_code=409, detail=THREAD_MISSING)
    try:
        await client.close_thread(thread_id)
    except ThreadMissing:
        await thread_service.mark_thread_missing(session, thread=row)
        await session.commit()
        raise HTTPException(status_code=409, detail=THREAD_MISSING)
    for run in await thread_service.runs_of(session, thread=row):
        if run.status in debate_service.ACTIVE_STATUSES:
            await debate_service.set_status(session, run_id=run.id, status="closed")
    await refresh_thread(session, row, client)
    payload = await _thread_public(session, row, host_id=client.host_id)
    await session.commit()
    return payload


@router.get("/{project_name}/threads/{thread_id}/events")
async def thread_events(
    project_name: str, thread_id: str, session: SessionDep
) -> EventSourceResponse:
    """
    Follow a thread: its posts, this install's run's activity, and the thread
    itself (status, live run, verdict).

    Emits a post whenever its payload changes, since provenance and tallies are
    written after a post exists. Stays open while the thread is viewed and not
    closed, so a teammate's run streams in as well as ours; a reconnecting
    client gets everything again.
    """
    project = await project_service.get_project_by_name(session, project_name)
    project_id = project.id
    await _require(session, project_id, thread_id)
    forum = await _client(session, project) if lab_enabled(project) else None
    host_id = forum.host_id if forum is not None else ""

    async def events() -> AsyncIterator[dict]:
        sent: dict[str, str] = {}
        last_activity: object = object()
        last_thread: str | None = None
        async with stream_session_factory() as stream:
            while True:
                if forum is not None:
                    await _maybe_refresh(stream, project_id, thread_id, forum)
                row = await thread_service.require_thread(
                    stream, project_id=project_id, thread_id=thread_id
                )
                for post in await thread_service.posts_with_notes(stream, thread=row):
                    payload = post.model_dump_json()
                    if sent.get(post.post_id) != payload:
                        sent[post.post_id] = payload
                        yield {"event": "post", "data": payload}

                public = await _thread_public(stream, row, host_id=host_id)
                activity = public.run.activity if public.run else None
                since = public.run.activity_since if public.run else None
                if activity != last_activity:
                    last_activity = activity
                    yield {
                        "event": "activity",
                        "data": json.dumps({"activity": activity, "since": since}),
                    }
                encoded = public.model_dump_json()
                if encoded != last_thread:
                    last_thread = encoded
                    yield {"event": "thread", "data": encoded}

                if row.status == "closed" or row.thread_missing:
                    return
                # SQLite caches within a transaction: without this the next poll
                # would re-read this one's snapshot.
                await stream.rollback()
                await asyncio.sleep(STREAM_POLL_SECONDS)

    return EventSourceResponse(events())
