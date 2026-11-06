"""
Hosted forums: their routes, and git's smart HTTP protocol in front of them.

The forum routes need a signed-in user, as every route does, and see only the
forums that user may use (`services/forums.py`); anything else is `404`. They
also need git 2.34 or later on the server: without it they answer `503`, and
items go on working.

The git routes run `git http-backend` as a CGI, one process per request, after
identity and access are settled. `REMOTE_USER` is the caller's `sub` (which
also turns on receive-pack, as git does for an authenticated user), and
`VISTA_PUSHER_NAME` their display name; the hooks read both
(`services/forum_hook.py`). The request body goes to its stdin as it arrives
and its output comes back as it is written, never held whole.
"""

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncIterator
from typing import Annotated as A

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from ..config import CollabSettings
from ..db.db import SessionDep
from ..db.schemas import ForumLink, ForumPublic, ForumPushPublic, ForumTable
from ..services import forums
from ..services.errors import NotFound
from ..services.identity import User, UserDep, get_user
from ..services.items import get_item

log = logging.getLogger("vista.collaboration")

router = APIRouter(tags=["forums"], dependencies=[Depends(get_user)])

_MAX_HEADER_BYTES = 64 * 1024


def _settings(request: Request) -> CollabSettings:
    return request.app.state.settings


def require_git(request: Request) -> None:
    """`503` unless the server has a git that can serve forums (found at startup)."""
    if not getattr(request.app.state, "git_ok", False):
        raise HTTPException(
            status_code=503,
            detail="This server has no usable git (2.34 or later), so it cannot host forums.",
        )


GitDep = Depends(require_git)


def _public(request: Request, forum: ForumTable) -> ForumPublic:
    base = str(request.base_url).rstrip("/")
    return ForumPublic(
        id=forum.id,
        git_url=f"{base}/git/{forum.id}.git",
        item_id=forum.item_id,
        created_at=forum.created_at,
    )


@router.post("/forums", status_code=201, dependencies=[GitDep])
async def create_forum(
    request: Request, session: SessionDep, user: UserDep
) -> ForumPublic:
    """A new, empty forum owned by the caller, and where to fetch and push it."""
    settings = _settings(request)
    forum = await forums.create_forum(session, user)
    try:
        await run_in_threadpool(forums.create_repo, settings.forums_dir, forum.id)
    except Exception:
        log.exception("Could not create the repository of forum %s", forum.id)
        await forums.delete_forum(session, forum.id, user)
        raise HTTPException(status_code=500, detail="Could not create the forum.")
    return _public(request, forum)


@router.put("/forums/{forum_id}/item", dependencies=[GitDep])
async def link_forum(
    forum_id: uuid.UUID,
    data: ForumLink,
    request: Request,
    session: SessionDep,
    user: UserDep,
) -> ForumPublic:
    """
    Link the caller's unlinked forum to a project item they can see, once; from
    then on the item's team is the forum's. `409` when it is linked already.
    """
    return _public(request, await forums.link(session, forum_id, data.item_id, user))


@router.get("/items/{item_id}/forum", dependencies=[GitDep])
async def get_item_forum(
    item_id: uuid.UUID, request: Request, session: SessionDep, user: UserDep
) -> ForumPublic:
    """The forum linked to an item the caller can see; `404` when it has none."""
    await get_item(session, item_id, user)
    forum = await forums.forum_of_item(session, item_id)
    if forum is None:
        raise NotFound("This item has no forum.")
    return _public(request, forum)


@router.delete("/forums/{forum_id}", status_code=204, dependencies=[GitDep])
async def delete_forum(
    forum_id: uuid.UUID, request: Request, session: SessionDep, user: UserDep
) -> None:
    """Delete the caller's forum, if it was never linked to an item (`409` otherwise)."""
    await forums.delete_forum(session, forum_id, user)
    await run_in_threadpool(forums.delete_repo, _settings(request).forums_dir, forum_id)


@router.get("/forums/{forum_id}/pushes", dependencies=[GitDep])
async def list_pushes(
    forum_id: uuid.UUID,
    session: SessionDep,
    user: UserDep,
    after: A[int, Query(ge=0)] = 0,
    limit: A[int, Query(ge=1, le=5000)] = 1000,
) -> list[ForumPushPublic]:
    """
    Who pushed each post, in the order the server took them: the records with a
    `seq` above `after`, for whoever may use the forum.
    """
    await forums.get_forum(session, forum_id, user)
    rows = await forums.pushes(session, forum_id, after, limit)
    return [ForumPushPublic.model_validate(r, from_attributes=True) for r in rows]


# ---------------------------------------------------------------------------
# git's smart HTTP
# ---------------------------------------------------------------------------


def _cgi_env(
    request: Request,
    settings: CollabSettings,
    forum_id: uuid.UUID,
    path_info: str,
    user: User,
) -> dict[str, str]:
    """
    What `git http-backend` and the hooks it starts run with: a clean
    environment (not the server's, and no user's git configuration), the CGI
    variables, and what the hooks need to reach the server's database and rules.
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if k in ("PATH", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL")
    }
    env["HOME"] = str(settings.forums_dir)
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_PROJECT_ROOT=str(settings.forums_dir),
        GIT_HTTP_EXPORT_ALL="1",
        PATH_INFO=path_info,
        QUERY_STRING=request.url.query,
        REQUEST_METHOD=request.method,
        REMOTE_USER=user.sub,
        REMOTE_ADDR=request.client.host if request.client else "",
        VISTA_PUSHER_NAME=user.name or "",
        VISTA_FORUM_ID=str(forum_id),
        # The hooks read the same settings as this process.
        VISTA_COLLAB_DATA_DIR=str(settings.data_dir),
        VISTA_COLLAB_DATABASE_URL=settings.database_url,
        VISTA_COLLAB_SQLITE_JOURNAL_MODE=settings.sqlite_journal_mode,
        VISTA_COLLAB_FORUM_RUN_EXPIRY_SECONDS=str(settings.forum_run_expiry_seconds),
    )
    for header, variable in (
        ("content-type", "CONTENT_TYPE"),
        ("content-length", "CONTENT_LENGTH"),
        ("content-encoding", "HTTP_CONTENT_ENCODING"),
        ("git-protocol", "HTTP_GIT_PROTOCOL"),
    ):
        if header in request.headers:
            env[variable] = request.headers[header]
    return env


async def _feed(request: Request, proc: asyncio.subprocess.Process) -> None:
    """The request body into the CGI's stdin, as it arrives."""
    assert proc.stdin is not None
    try:
        async for chunk in request.stream():
            proc.stdin.write(chunk)
            await proc.stdin.drain()
    except BrokenPipeError, ConnectionResetError:
        pass  # the CGI stopped reading: its output says why
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass


async def _drain_stderr(proc: asyncio.subprocess.Process, forum_id: uuid.UUID) -> None:
    assert proc.stderr is not None
    data = await proc.stderr.read()
    if data:
        log.warning(
            "git http-backend, forum %s: %s",
            forum_id,
            data[-2000:].decode("utf-8", "replace").strip(),
        )


def _split_cgi_headers(
    buffer: bytes,
) -> tuple[int, list[tuple[str, str]], bytes] | None:
    """The status, headers and the rest of the body, once the CGI's header block is complete."""
    for sep in (b"\r\n\r\n", b"\n\n"):
        end = buffer.find(sep)
        if end != -1:
            head, rest = buffer[:end], buffer[end + len(sep) :]
            break
    else:
        return None
    status = 200
    headers: list[tuple[str, str]] = []
    for line in head.decode("latin-1").splitlines():
        name, _, value = line.partition(":")
        if not value:
            continue
        value = value.strip()
        if name.lower() == "status":
            status = int(value.split()[0])
        else:
            headers.append((name.strip(), value))
    return status, headers, rest


async def _serve_git(
    request: Request, forum_id: str, path_info: str, session: SessionDep, user: User
) -> StreamingResponse:
    settings = _settings(request)
    parsed = forums.parse_forum_id(forum_id)
    await forums.get_forum(session, parsed, user)
    await session.rollback()  # no transaction held across a push
    require_git(request)
    await run_in_threadpool(forums.create_repo, settings.forums_dir, parsed)
    proc = await asyncio.create_subprocess_exec(
        "git",
        "http-backend",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_cgi_env(request, settings, parsed, path_info, user),
    )
    feeder = asyncio.create_task(_feed(request, proc))
    drain = asyncio.create_task(_drain_stderr(proc, parsed))
    assert proc.stdout is not None
    buffer = b""
    parsed_head = None
    try:
        while parsed_head is None:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            buffer += chunk
            parsed_head = _split_cgi_headers(buffer)
            if parsed_head is None and len(buffer) > _MAX_HEADER_BYTES:
                break
    except BaseException:
        await _finish(proc, feeder, drain)
        raise
    if parsed_head is None:
        await _finish(proc, feeder, drain)
        raise HTTPException(status_code=502, detail="git produced no answer.")
    status, headers, first = parsed_head

    async def body() -> AsyncIterator[bytes]:
        try:
            if first:
                yield first
            assert proc.stdout is not None
            while chunk := await proc.stdout.read(65536):
                yield chunk
        finally:
            await _finish(proc, feeder, drain)

    response = StreamingResponse(body(), status_code=status)
    response.raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers
    ]
    return response


async def _finish(
    proc: asyncio.subprocess.Process, feeder: asyncio.Task, drain: asyncio.Task
) -> None:
    """Let the CGI finish if it is about to, and stop it and its helpers if not."""
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
    for task in (feeder, drain):
        if not task.done():
            task.cancel()
    await asyncio.gather(feeder, drain, return_exceptions=True)


@router.get("/git/{forum_id}.git/info/refs")
async def git_info_refs(
    forum_id: str, request: Request, session: SessionDep, user: UserDep
) -> StreamingResponse:
    return await _serve_git(
        request, forum_id, f"/{forum_id}.git/info/refs", session, user
    )


@router.post("/git/{forum_id}.git/git-upload-pack")
async def git_upload_pack(
    forum_id: str, request: Request, session: SessionDep, user: UserDep
) -> StreamingResponse:
    return await _serve_git(
        request, forum_id, f"/{forum_id}.git/git-upload-pack", session, user
    )


@router.post("/git/{forum_id}.git/git-receive-pack")
async def git_receive_pack(
    forum_id: str, request: Request, session: SessionDep, user: UserDep
) -> StreamingResponse:
    return await _serve_git(
        request, forum_id, f"/{forum_id}.git/git-receive-pack", session, user
    )
