"""
Debate API — open a debate, watch it argue, join it, end it.

Debates are scoped under a project, so project membership is the access boundary
(enforced by `project_service.get_project_by_name`), matching the campaign routes.
Routes stay thin: the lifecycle is in `services/debate.py` and the argument is in
`agents/forum/`.

Opening a debate returns as soon as the thread and its roster exist. The argument
itself then runs in the background for minutes, and the client follows it on the
event stream — a request that blocked until the Referee ruled would be a request
nobody could sensibly time out.
"""

import asyncio
import json
import logging
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from sse_starlette.sse import EventSourceResponse

from ..agents.forum import simulation
from ..agents.forum.project_forum import (
    build_client_for,
    forum_url_of,
    lab_enabled,
)
from ..agents.forum.wiring import continue_debate_task, run_debate_task
from ..db.db import SessionDep, get_engine
from ..db.schemas import (
    DebateCreate,
    DebateParticipantPublic,
    DebatePostPublic,
    DebateRunPublic,
    DebateStatePublic,
)
from ..services import debate as debate_service
from ..services import project as project_service
from ..services.auth import UserDep
from ..services.forum_git import (
    POSTABLE_KINDS,
    ForumClient,
    ForumDisabled,
    PostKind,
    ThreadClosed,
    ThreadMissing,
)
from ..services.git_check import git_status
from sqlmodel.ext.asyncio.session import AsyncSession


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["debates"])


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


@router.get("/{project_name}/forum/status")
async def forum_status(
    project_name: str, session: SessionDep, user: UserDep
) -> ForumStatus:
    """
    This project's forum state, for the page to explain itself with.

    Project-scoped because the repository is: a project with no forum URL has no
    Hypothesis Lab, and `enabled: false` is how the page knows to say so rather
    than offering a debate that has nowhere to publish. `git_ok: false` is the
    other reason the lab can be off, and `git_reason` says what to do about it.

    Never raises on a forum that is off or unreachable: this endpoint exists to
    report bad states, so failing on one would defeat it.
    """
    project = await project_service.get_project_by_name(session, project_name, user)
    git = git_status()
    if not lab_enabled(project):
        return ForumStatus(
            enabled=False, shared=False, git_ok=git.ok, git_reason=git.reason
        )

    client = build_client_for(project)
    url = forum_url_of(project)
    try:
        # The repository's own remote, not the project row's intention: they
        # disagree exactly when it matters, e.g. a save that could not set it.
        remote = await client.remote()
        unpublished = await client.unpublished()
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
    )


def stream_session_factory() -> AsyncSession:
    """
    The session the event stream reads through.

    A module-level factory rather than the request's session, because the
    generator outlives the handler that created it — and rather than a direct
    `AsyncSession(get_engine())`, because that hardcodes the production engine
    into a code path tests need to point somewhere else. Same shape the campaign
    monitor uses.
    """
    return AsyncSession(get_engine())


_BACKGROUND: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    """
    Run a debate in the background, holding a reference to its task.

    asyncio keeps only a weak reference to a running task, so a task nobody
    holds can be garbage-collected mid-run. Debates take minutes; dropping one
    would look like a debate that silently stopped arguing.
    """
    task = asyncio.create_task(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task


FORUM_REFRESH_SECONDS = 10.0
"""
How often a watched debate is re-read from the forum itself.

Much slower than the DB poll below, and deliberately. A forum read runs git
and, on a shared remote, a fetch — cheap locally, not free across a network. Our own agents' posts reach the projection through the debate
task without any of this; the refresh is what makes a *peer's* comment appear, and ten
seconds is fast enough for a human conversation.
"""


_last_refresh: dict[str, float] = {}
_refresh_locks: dict[str, asyncio.Lock] = {}


async def _maybe_refresh(session: AsyncSession, run, client: ForumClient) -> bool:
    """
    Re-read one debate from the forum, at most once per interval across all viewers.

    Both halves matter with several people watching a debate: the lock stops
    concurrent fetches of the same thread, and the timestamp stops the queue
    behind it from each doing the fetch again the moment it is released.

    Returns whether it committed, because a commit expires every object the
    session holds — the caller's `run` included — and reading a field off it
    afterwards is async IO in a context that cannot await.
    """
    key, run_id = run.thread_id, run.id
    lock = _refresh_locks.setdefault(key, asyncio.Lock())
    if lock.locked():
        return False
    async with lock:
        now = asyncio.get_running_loop().time()
        if now - _last_refresh.get(key, 0.0) < FORUM_REFRESH_SECONDS:
            return False
        _last_refresh[key] = now
        try:
            await debate_service.refresh_from_forum(session, client, run)
            await session.commit()
            return True
        except Exception:  # noqa: BLE001 — a stream must survive a bad fetch
            logger.warning("debate %s: forum refresh failed", run_id, exc_info=True)
            await session.rollback()
            return False


STREAM_POLL_SECONDS = 1.0
"""
How often the event stream looks for new posts.

The stream reads the DB projection rather than subscribing to the orchestrator in
memory, so it keeps working when the debate runs in a different worker from the
one serving the stream, and a reconnecting client resumes from a post id instead
of from a subscription it lost.
"""


class HumanPost(BaseModel):
    """What the human says into a live debate."""

    body: str
    kind: PostKind = PostKind.ASK

    @field_validator("kind")
    @classmethod
    def _postable(cls, kind: PostKind) -> PostKind:
        """
        Refuse a kind the operator may not post.

        `TASK`, `CLOSED` and the votes are real kinds that appear in threads but
        are written by their own verbs. Rejecting here makes that a 422 rather
        than a forum error.
        """
        if kind not in POSTABLE_KINDS:
            raise ValueError(
                f"{kind} cannot be posted; use one of "
                f"{', '.join(sorted(str(k) for k in POSTABLE_KINDS))}"
            )
        return kind


@router.post("/{project_name}/debates")
async def open_debate(
    project_name: str, body: DebateCreate, session: SessionDep, user: UserDep
) -> DebateRunPublic:
    """
    Open a thread, attach the roster, and start arguing in the background.
    """
    project = await project_service.get_project_by_name(session, project_name, user)

    from ..agents.forum.wiring import build_orchestrator

    try:
        run = await build_orchestrator(build_client_for(project)).start(
            session,
            project_id=project.id,
            user_id=user.id,
            topic=body.topic,
            framing=body.framing,
            rounds=body.rounds,
        )
    except ForumDisabled as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    # Read everything off the ORM object *before* committing. `commit()` expires
    # it, so a later attribute access tries to refresh it — which is async IO in
    # a context that cannot await, and fails with MissingGreenlet rather than
    # anything that names the real problem.
    run_id = run.id
    payload = DebateRunPublic.model_validate(run)

    # Commit before handing the run to a task with its own session, or that task
    # would look for a run this one has not written yet.
    await session.commit()
    _spawn(run_debate_task(run_id))
    return payload


@router.get("/{project_name}/debates")
async def list_debates(
    project_name: str, session: SessionDep, user: UserDep
) -> list[DebateRunPublic]:
    project = await project_service.get_project_by_name(session, project_name, user)
    runs = await debate_service.list_debates(session, project_id=project.id)
    return [DebateRunPublic.model_validate(r) for r in runs]


@router.get("/{project_name}/debates/{run_id}")
async def get_debate(
    project_name: str, run_id: uuid.UUID, session: SessionDep, user: UserDep
) -> DebateStatePublic:
    project = await project_service.get_project_by_name(session, project_name, user)
    project_id = project.id  # the refresh below commits, which expires `project`
    # Built before the refresh, for the same reason: a commit expires the project
    # row, and a client cannot be made from an expired one.
    lab = build_client_for(project) if lab_enabled(project) else None
    run = await _require(session, run_id, project_id)

    # Read the forum here too, not only from the event stream.
    #
    # The stream ends the moment a run reaches a terminal status, and the UI does
    # not open one for a run that is already finished — so for a debate that has
    # argued itself out, the stream refresh never runs again. That is precisely
    # when an outside reviewer is most likely to comment: there is a hypothesis
    # on the table worth objecting to. Without this, their post never appeared at
    # all. The throttle is shared with the stream, so watching a live debate does
    # not fetch twice.
    if lab is not None and await _maybe_refresh(session, run, lab):
        run = await _require(session, run_id, project_id)

    return DebateStatePublic(
        run=DebateRunPublic.model_validate(run),
        participants=[
            DebateParticipantPublic.model_validate(p)
            for p in await debate_service.list_participants(session, run_id=run.id)
        ],
        posts=[
            DebatePostPublic.model_validate(p)
            for p in await debate_service.list_posts(session, run_id=run.id)
        ],
        simulations=[
            record.model_dump()
            for record in await simulation.commissioned_runs(
                session, debate_run_id=run_id
            )
        ],
    )


@router.post("/{project_name}/debates/{run_id}/posts")
async def post_to_debate(
    project_name: str,
    run_id: uuid.UUID,
    body: HumanPost,
    session: SessionDep,
    user: UserDep,
) -> DebatePostPublic:
    """
    Say something into a live debate, as the human.

    Attributed to `human`, which is exactly right here and exactly wrong for an
    agent. The agents pick it up when they next read the thread — there is no
    separate inbox, because the thread already is one.

    A closed thread takes no more posts, from anyone: readers ignore anything
    after the first CLOSED, so a post there would be written and never shown.
    A thread no longer on the forum takes none either.
    """
    project = await project_service.get_project_by_name(session, project_name, user)
    run = await _require(session, run_id, project.id)
    if run.thread_missing:
        raise HTTPException(status_code=409, detail=THREAD_MISSING)

    client = build_client_for(project)
    try:
        post = await client.post_as_human(run.thread_id, body.body, kind=body.kind)
    except ForumDisabled as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ThreadClosed:
        raise HTTPException(status_code=409, detail="This thread is closed.")
    except ThreadMissing:
        await debate_service.mark_thread_missing(session, run_id=run.id)
        await session.commit()
        raise HTTPException(status_code=409, detail=THREAD_MISSING)

    thread = await client.read_thread(run.thread_id)
    await debate_service.project_thread(session, run_id=run.id, thread=thread)

    row = next(
        (
            p
            for p in await debate_service.list_posts(session, run_id=run.id)
            if p.post_id == post.id
        ),
        None,
    )
    if row is None:
        raise HTTPException(status_code=500, detail="The post did not reach the thread")

    # We know who this was: the request was authenticated. The forum cannot carry
    # that — the post says `human` and has nowhere to put a name — so it is
    # recorded here, on the one path where it is knowledge rather than a claim.
    await debate_service.record_author(
        session, run_id=run.id, post_id=post.id, authored_by=user.email
    )
    await session.refresh(row)
    return DebatePostPublic.model_validate(row)


class ContinueDebate(BaseModel):
    """How much more arguing to buy."""

    rounds: int = Field(default=3, ge=1, le=10)


@router.post("/{project_name}/debates/{run_id}/continue")
async def continue_debate(
    project_name: str,
    run_id: uuid.UUID,
    body: ContinueDebate,
    session: SessionDep,
    user: UserDep,
) -> DebateRunPublic:
    """
    Argue a finished debate for a few more rounds.

    The case this is for: a debate concluded, and then someone — a peer reviewer,
    or the operator reading the verdict — raised an objection the agents never
    answered. Opening a fresh debate would lose the argument that produced the
    objection; this keeps the thread and picks it up.

    Refused on a debate that is still arguing, on a closed one — a closed thread
    takes no more posts, so a roster there could not speak — and on one whose
    thread is no longer on the forum.
    """
    project = await project_service.get_project_by_name(session, project_name, user)
    run = await _require(session, run_id, project.id)

    if run.thread_missing:
        raise HTTPException(status_code=409, detail=THREAD_MISSING)
    if run.status in debate_service.ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="This debate is still arguing.")
    if run.status == "closed":
        raise HTTPException(
            status_code=409,
            detail="This thread is closed; it accepts no further posts.",
        )
    if not lab_enabled(project):
        raise HTTPException(
            status_code=503,
            detail=f"Project {project.name!r} has no Hypothesis Lab.",
        )

    payload = DebateRunPublic.model_validate(run)
    extra = body.rounds
    await session.commit()
    _spawn(continue_debate_task(run_id, extra))
    return payload


@router.post("/{project_name}/debates/{run_id}/close")
async def close_debate(
    project_name: str, run_id: uuid.UUID, session: SessionDep, user: UserDep
) -> DebateRunPublic:
    """
    End a debate early.

    Closing is the forum's, not the orchestrator's: it writes a CLOSED post, and
    the next agent post is refused, so the running loop finds out by being told
    no. The status is set here so a client sees the change immediately rather
    than waiting for the loop to notice.
    """
    project = await project_service.get_project_by_name(session, project_name, user)
    run = await _require(session, run_id, project.id)
    if run.thread_missing:
        raise HTTPException(status_code=409, detail=THREAD_MISSING)

    try:
        await build_client_for(project).close_thread(run.thread_id)
    except ForumDisabled as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ThreadMissing:
        await debate_service.mark_thread_missing(session, run_id=run.id)
        await session.commit()
        raise HTTPException(status_code=409, detail=THREAD_MISSING)

    run = await debate_service.set_status(session, run_id=run.id, status="closed")
    return DebateRunPublic.model_validate(run)


@router.get("/{project_name}/debates/{run_id}/events")
async def debate_events(
    project_name: str, run_id: uuid.UUID, session: SessionDep, user: UserDep
) -> EventSourceResponse:
    """
    Follow a debate as it argues.

    Emits a post whenever its payload changes — on arrival, and again each time
    something on it moves. A client that reconnects gets everything again from the
    start of the thread, which is cheap here and simpler than resumable cursors —
    a debate is a few dozen posts, not a log.

    Re-emitting is not a nicety. Only `body` is fixed once a post exists; the rest
    of the row is written afterwards. Provenance arrives a beat late by
    construction — the forum records what was said and has no field for how the
    agent got there, so `record_post_tools` writes it onto a row the projection
    has already created — and a vote tally moves for as long as the thread is
    open, because a peer can upvote something from three rounds ago. Sending each
    post once meant a post the stream happened to catch inside that gap read
    "based on model alone" for the rest of the connection, and only corrected when
    the run ended and the client went back to fetching whole states.
    """
    project = await project_service.get_project_by_name(session, project_name, user)
    await _require(session, run_id, project.id)
    project_id = project.id
    # Resolved once, outside the generator: the project row belongs to the
    # request's session, which closes as soon as the response starts streaming.
    # `None` when this project has no lab — the stream still runs, it just has no
    # forum to re-read for peer posts.
    forum = build_client_for(project) if lab_enabled(project) else None

    async def events() -> AsyncIterator[dict]:
        # post_id -> the payload last sent for it. The payload itself is the
        # fingerprint, deliberately: a hand-written list of "the fields that can
        # change" is a list to forget to update, and forgetting means the field
        # added next is silently invisible to every live viewer. This cannot miss
        # one, and it re-sends only when something really moved.
        sent: dict[str, str] = {}
        last_activity: str | None | object = object()  # never equal to a real value
        # Its own session: this generator outlives the request handler, and the
        # request's session is closed as soon as the response starts streaming.
        async with stream_session_factory() as stream_session:
            while True:
                run = await debate_service.require_debate_in_project(
                    stream_session, run_id=run_id, project_id=project_id
                )
                # Peers publish to the remote, not to us. Something has to look.
                if forum is not None and await _maybe_refresh(
                    stream_session, run, forum
                ):
                    run = await debate_service.require_debate_in_project(
                        stream_session, run_id=run_id, project_id=project_id
                    )
                posts = await debate_service.list_posts(stream_session, run_id=run_id)
                for post in posts:
                    payload = DebatePostPublic.model_validate(post).model_dump_json()
                    if sent.get(post.post_id) == payload:
                        continue
                    sent[post.post_id] = payload
                    yield {"event": "post", "data": payload}

                # Emit the activity line whenever it changes. Without it the
                # page shows a thread that stops growing and no indication that
                # anything is still happening — which is indistinguishable from
                # a crash, and now that a role can wait on a cluster job the
                # silence lasts minutes.
                if run.activity != last_activity:
                    last_activity = run.activity
                    yield {
                        "event": "activity",
                        "data": json.dumps(
                            {
                                "activity": run.activity,
                                "since": run.activity_since,
                            }
                        ),
                    }

                if run.status not in debate_service.ACTIVE_STATUSES:
                    yield {
                        "event": "status",
                        "data": DebateRunPublic.model_validate(run).model_dump_json(),
                    }
                    return

                # SQLite/SQLAlchemy caches within a transaction, so the next poll
                # would re-read this one's snapshot and never see the background
                # task's writes.
                await stream_session.rollback()
                await asyncio.sleep(STREAM_POLL_SECONDS)

    return EventSourceResponse(events())


THREAD_MISSING = "This thread is no longer on the forum."


async def _require(session, run_id: uuid.UUID, project_id: uuid.UUID):
    try:
        return await debate_service.require_debate_in_project(
            session, run_id=run_id, project_id=project_id
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Debate not found")
