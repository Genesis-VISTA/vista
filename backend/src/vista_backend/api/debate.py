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
from ..agents.forum.wiring import build_client, continue_debate_task, run_debate_task
from ..db.db import SessionDep, get_engine
from ..db.schemas import (
    DebateCreate,
    DebateParticipantPublic,
    DebatePostPublic,
    DebateRunPublic,
    DebateStatePublic,
    EnrolledOrigin,
)
from ..services import debate as debate_service
from ..services import project as project_service
from ..services.auth import UserDep
from ..config import settings
from ..services.h5i_forum import (
    POSTABLE_KINDS,
    ForumDisabled,
    PostKind,
    VotePolicy,
)
from sqlmodel.ext.asyncio.session import AsyncSession


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["debates"])

# The forum's configuration is deployment-wide rather than per-project, so it
# gets its own prefix instead of hiding global state under a project path.
forum_router = APIRouter(prefix="/forum", tags=["debates"])


class ForumStatus(BaseModel):
    """Whether this forum is shared, and whether its votes are being counted."""

    enabled: bool
    shared: bool
    """Publishing to a remote — i.e. outside participants can reach it."""

    remote: str | None = None
    vote_policy: str | None = None
    enrolled: int = 0
    votes_counting: bool = True
    """
    False when the policy is `principal` and nobody has enrolled.

    That combination discards every vote on the forum, including the debate
    agents' own, and nothing about a thread shows it — which is exactly why it
    is surfaced here.
    """


@forum_router.get("/status")
async def forum_status(user: UserDep) -> ForumStatus:
    """
    The forum's federation state, for the UI to warn about.

    Never raises on a forum that is off or unreachable: this endpoint exists to
    report bad states, so failing on one would defeat it.
    """
    config = settings.forum
    if not config.enabled or config.repo_root is None:
        return ForumStatus(enabled=False, shared=False)

    client = build_client()
    try:
        described = await client.remote()
        policy = await client.vote_policy()
        enrolled = len(await client.enrollments())
    except Exception:  # noqa: BLE001
        logger.warning("forum: could not read federation status", exc_info=True)
        return ForumStatus(enabled=True, shared=bool(config.remote_url))

    # Whether the forum is shared is h5i's answer, not the configuration's. The
    # two disagree exactly when it matters: `ensure_federation` logs and carries
    # on if the remote is unreachable at boot, so a configured `remote_url` can
    # sit alongside a forum still publishing only to its local bare repo. Trust
    # the setting and this endpoint reports a shared forum that nobody can reach.
    #
    # Matching the URL rather than h5i's prose also catches the remote being
    # pointed somewhere other than the configured one — by hand, or by an
    # earlier run under different settings.
    shared = bool(config.remote_url) and config.remote_url in described

    return ForumStatus(
        enabled=True,
        shared=shared,
        remote=config.remote_url if shared else None,
        vote_policy=str(policy),
        enrolled=enrolled,
        votes_counting=not (policy == VotePolicy.PRINCIPAL and enrolled == 0),
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

Much slower than the DB poll below, and deliberately. A forum read spawns a
subprocess and, on a shared remote, a git fetch — cheap locally, not free across
a network. Our own agents' posts reach the projection through the debate task
without any of this; the refresh is what makes a *peer's* comment appear, and ten
seconds is fast enough for a human conversation.
"""


_last_refresh: dict[str, float] = {}
_refresh_locks: dict[str, asyncio.Lock] = {}


ENROLLMENT_CACHE_SECONDS = 60.0
"""
How long the origin→account map is reused.

Enrollment is a once-per-machine act, so this changes on the timescale of people
joining a forum, not of posts arriving. Reading it is another subprocess on a
path that already pays for a forum read, and the cost of being a minute stale is
that one new participant's name appears a minute late.
"""

_enrollments: tuple[float, dict[str, EnrolledOrigin]] | None = None


async def _enrolled_origins() -> dict[str, EnrolledOrigin]:
    """
    Which machines have bound themselves to a forge account.

    Returns an empty map on any failure. Naming is a courtesy on top of a
    readable thread; a forum whose enrollments cannot be read should still show
    its posts, with origins unresolved exactly as they were before.
    """
    global _enrollments
    now = asyncio.get_running_loop().time()
    if _enrollments is not None and now - _enrollments[0] < ENROLLMENT_CACHE_SECONDS:
        return _enrollments[1]
    try:
        rows = await build_client().enrollments()
    except Exception:  # noqa: BLE001
        logger.warning("forum: could not read enrollments", exc_info=True)
        return {}
    resolved = {
        row.origin: EnrolledOrigin(principal=row.principal, name=row.name)
        for row in rows
        if row.origin and row.principal
    }
    _enrollments = (now, resolved)
    return resolved


async def _maybe_refresh(session: AsyncSession, run) -> bool:
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
            await debate_service.refresh_from_forum(session, build_client(), run)
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
        Refuse a kind h5i would accept and then drop.

        `CLAIM` and friends are real kinds that appear in threads but are
        produced by other verbs; `post --kind CLAIM` exits 0 and publishes
        nothing. Rejecting here turns that into a 422 instead of a post the
        client is told about and can never see.
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
        run = await build_orchestrator().start(
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
    if await _maybe_refresh(session, run):
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
        enrolled_origins=await _enrolled_origins(),
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

    This is the one legitimate use of host-side posting: it is attributed to
    `human`, which is exactly right here and exactly wrong for an agent. The
    agents pick it up when they next read the thread — there is no separate
    inbox, because the thread already is one.

    A closed debate still accepts the human's posts, because h5i's own rule is
    that closing removes the thread from every *box's* inbox and leaves the
    host's write path open. Closing is the human's verb; it ends the argument,
    not their ability to annotate the record.
    """
    project = await project_service.get_project_by_name(session, project_name, user)
    run = await _require(session, run_id, project.id)

    client = build_client()
    try:
        post = await client.post_as_human(run.thread_id, body.body, kind=body.kind)
    except ForumDisabled as exc:
        raise HTTPException(status_code=503, detail=str(exc))

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
    # that — h5i stamps `sender="human"` and has nowhere to put a name — so it is
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

    Refused on a debate that is still arguing, and on a closed one. Closing is
    final in h5i — the thread is in the attic and accepts no posts — so a
    "continue" there would attach a roster that could not speak.
    """
    project = await project_service.get_project_by_name(session, project_name, user)
    run = await _require(session, run_id, project.id)

    if run.status in debate_service.ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="This debate is still arguing.")
    if run.status == "closed":
        raise HTTPException(
            status_code=409,
            detail="This thread is closed; h5i accepts no further posts on it.",
        )
    if not settings.forum.enabled or settings.forum.repo_root is None:
        raise HTTPException(status_code=503, detail="The forum is not configured.")

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

    Closing is h5i's, not the orchestrator's: the thread leaves every box's inbox
    and the next agent post is refused, so the running loop finds out by being
    told no. The status is set here so a client sees the change immediately
    rather than waiting for the loop to notice.
    """
    project = await project_service.get_project_by_name(session, project_name, user)
    run = await _require(session, run_id, project.id)

    try:
        await build_client().close_thread(run.thread_id)
    except ForumDisabled as exc:
        raise HTTPException(status_code=503, detail=str(exc))

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
                if await _maybe_refresh(stream_session, run):
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


async def _require(session, run_id: uuid.UUID, project_id: uuid.UUID):
    try:
        return await debate_service.require_debate_in_project(
            session, run_id=run_id, project_id=project_id
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Debate not found")
