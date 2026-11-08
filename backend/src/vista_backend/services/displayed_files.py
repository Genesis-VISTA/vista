"""
The record of files `display_file` showed in a chat run (project-files).

A row in `displayed_file` is written per successful display, with the file's size and
`mtime_ns` at that moment. This module maps a sandbox path to the `(kind, path)` the files API
uses, decides from the stored stat whether a record's file is still the one shown, and reads
the records back for a chat's feed and for the Files page's "shown in".
"""

import mimetypes
import uuid
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import quote, unquote, urlsplit

from pydantic import BaseModel
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import ChatSessionTable, DisplayedFileTable
from ..utils.misc import path_is_under
from . import chat_session as chat_session_service
from . import project as project_service
from .project_agent import get_project_agent_key, project_agent_pool


FileKind = Literal["uploads", "outputs"]
DisplayStatus = Literal["current", "changed", "missing"]

# The sandbox roots `display_file` maps to the files API (the backend's `uri_map`).
_ROOTS: tuple[tuple[str, FileKind], ...] = (
    ("/mnt/data/output/", "outputs"),
    ("/mnt/data/uploads/", "uploads"),
)


def sandbox_path_to_file(uri: str) -> tuple[FileKind, str] | None:
    """
    Map the `uri` argument of a `display_file` call to `(kind, relative POSIX path)`.

    Accepts what the MCP server's `resolve_uri` accepts (a bare absolute path or a `file:` URI
    with no host or `localhost`, percent-encoded or not) and refuses what it refuses. Returns
    `None` for a path outside the two roots, a URI that does not normalise, or an empty rest.
    Sandbox paths are POSIX whatever the host is, so this never goes through `Path`.
    """
    if uri.startswith("/"):
        uri = "file://" + quote(uri)
    if not uri.startswith("file:"):
        return None

    parts = urlsplit(uri)
    decoded = unquote(parts.path)
    path = PurePosixPath(decoded)
    if (
        parts.netloc not in ("", "localhost")
        or not path.is_absolute()
        or ".." in path.parts
        or "." in parts.path.split("/")
    ):
        return None

    for prefix, kind in _ROOTS:
        if decoded.startswith(prefix):
            rest = decoded[len(prefix) :]
            return (kind, rest) if rest else None
    return None


def file_status(files_dir: Path, path: str, size: int, mtime_ns: int) -> DisplayStatus:
    """
    Whether `path` under `files_dir` is still the file recorded: `missing` if it is gone,
    `current` if its size and `st_mtime_ns` both match, else `changed`.
    """
    target = files_dir / path
    if not path_is_under(files_dir, target):
        return "missing"
    try:
        stat = target.stat()
    except OSError:
        return "missing"
    if not target.is_file():
        return "missing"
    if stat.st_size == size and stat.st_mtime_ns == mtime_ns:
        return "current"
    return "changed"


class DisplayedFileInfo(BaseModel):
    kind: FileKind
    path: str
    name: str
    mime_type: str
    run_id: str
    tool_call_id: str
    shown_at: str
    status: DisplayStatus


class ShownIn(BaseModel):
    chat_session_id: uuid.UUID
    chat_title: str
    run_id: str
    tool_call_id: str


async def list_displayed_files(
    session: AsyncSession, project_name: str, chat_session_id: uuid.UUID
) -> list[DisplayedFileInfo]:
    """A chat's records, newest first, each with the status of its file now."""
    project = await project_service.get_project_by_name(session, project_name)
    chat = await chat_session_service.get_chat_session(
        session, project_id=project.id, chat_session_id=chat_session_id
    )
    result = await session.exec(
        select(DisplayedFileTable)
        .where(DisplayedFileTable.chat_session_id == chat.id)
        .order_by(col(DisplayedFileTable.shown_at).desc())
    )
    rows = list(result.all())
    if not rows:
        return []

    agent_key = await get_project_agent_key(session, project_id=project.id)
    async with project_agent_pool.get(agent_key) as agent:
        dirs: dict[str, Path] = {
            "uploads": agent.uploads_dir,
            "outputs": agent.output_dir,
        }
        return [
            DisplayedFileInfo(
                kind=row.kind,  # type: ignore[arg-type]
                path=row.path,
                name=PurePosixPath(row.path).name,
                mime_type=mimetypes.guess_type(row.path)[0]
                or "application/octet-stream",
                run_id=row.run_id,
                tool_call_id=row.tool_call_id,
                shown_at=row.shown_at,
                status=file_status(dirs[row.kind], row.path, row.size, row.mtime_ns),
            )
            for row in rows
        ]


async def newest_records(
    session: AsyncSession, project_id: uuid.UUID, kind: FileKind
) -> dict[str, tuple[DisplayedFileTable, str]]:
    """
    The newest record for each displayed path of a kind, with its chat's title, in one query.
    """
    result = await session.exec(
        select(DisplayedFileTable, ChatSessionTable.title)
        .join(
            ChatSessionTable,
            col(ChatSessionTable.id) == col(DisplayedFileTable.chat_session_id),
        )
        .where(
            DisplayedFileTable.project_id == project_id,
            DisplayedFileTable.kind == kind,
        )
        .order_by(col(DisplayedFileTable.shown_at).desc())
    )
    newest: dict[str, tuple[DisplayedFileTable, str]] = {}
    for row, title in result.all():
        newest.setdefault(row.path, (row, title))
    return newest


def shown_in(
    record: tuple[DisplayedFileTable, str] | None, size: int, mtime_ns: int
) -> ShownIn | None:
    """`record` as a `ShownIn`, only when the file is still as that newest record saw it."""
    if record is None:
        return None
    row, title = record
    if row.size != size or row.mtime_ns != mtime_ns:
        return None
    return ShownIn(
        chat_session_id=row.chat_session_id,
        chat_title=title,
        run_id=row.run_id,
        tool_call_id=row.tool_call_id,
    )
