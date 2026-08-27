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
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
from sse_starlette.sse import EventSourceResponse

from ..agents.forum.wiring import build_client, run_debate_task
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
from ..services.h5i_forum import POSTABLE_KINDS, ForumDisabled, PostKind
from sqlmodel.ext.asyncio.session import AsyncSession


router = APIRouter(prefix="/projects", tags=["debates"])


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

    # Commit before handing the run to a task with its own session, or that task
    # would look for a run this one has not written yet.
    await session.commit()
    asyncio.create_task(run_debate_task(run.id))
    return DebateRunPublic.model_validate(run)


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
    run = await _require(session, run_id, project.id)
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
    return DebatePostPublic.model_validate(row)


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

    Emits each new post once, then a terminal `status` event when the run stops.
    A client that reconnects gets everything again from the start of the thread,
    which is cheap here and simpler than resumable cursors — a debate is a few
    dozen posts, not a log.
    """
    project = await project_service.get_project_by_name(session, project_name, user)
    await _require(session, run_id, project.id)
    project_id = project.id

    async def events() -> AsyncIterator[dict]:
        seen: set[str] = set()
        # Its own session: this generator outlives the request handler, and the
        # request's session is closed as soon as the response starts streaming.
        async with stream_session_factory() as stream_session:
            while True:
                run = await debate_service.require_debate_in_project(
                    stream_session, run_id=run_id, project_id=project_id
                )
                posts = await debate_service.list_posts(stream_session, run_id=run_id)
                for post in posts:
                    if post.post_id in seen:
                        continue
                    seen.add(post.post_id)
                    yield {
                        "event": "post",
                        "data": DebatePostPublic.model_validate(post).model_dump_json(),
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
