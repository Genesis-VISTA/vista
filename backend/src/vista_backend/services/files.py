import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.agents import ProjectAgent
from ..config import settings
from ..utils.misc import path_is_under, write_file_unique
from . import project as project_service
from ._helpers import ServiceUser
from .project_agent import get_project_agent_key, project_agent_pool


FileKind = Literal["uploads", "outputs"]


class FileInfo(BaseModel):
    name: str
    size: int
    created: datetime
    modified: datetime


def _kind_dir(agent: ProjectAgent, kind: FileKind) -> Path:
    """ Resolve the on-disk directory for a file kind on the given agent. """
    return agent.uploads_dir if kind == "uploads" else agent.output_dir


def _sanitize_filename(name: str | None) -> str:
    """
    Sanitize a path. Remove special characters, any . or .. components, and ensure its a relative
    path.
    """
    name = name or ""
    cleaned = "/".join([
        re.sub(r"[^A-Za-z0-9._-]", "_", Path(part).name)
        for part in name.split("/")
        if part and not re.fullmatch(r'\.+', part)
    ])
    if not cleaned:
        cleaned = "upload.bin"
    return cleaned


def _get_file(files_dir: Path, name: str) -> Path:
    """ Return file path, checks for path traversal etc. and that it exists. """
    if _sanitize_filename(name) != name:
        raise HTTPException(status_code=404, detail="Not found")
    path = files_dir / name
    if not path_is_under(files_dir, path) or not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return path


async def list_files(
    session: AsyncSession, project_name: str, kind: FileKind, user: ServiceUser
) -> list[FileInfo]:
    project = await project_service.get_project_by_name(session, project_name, user)
    agent_key = await get_project_agent_key(session, project_id=project.id, user_id=user.id)
    async with project_agent_pool.get(agent_key) as agent:
        files_dir = _kind_dir(agent, kind)
        if not files_dir.exists():
            return []

        files: list[FileInfo] = []
        for file in files_dir.rglob("*"):
            # Skip any symlink that escapes files_dir
            if not file.is_file() or not path_is_under(files_dir, file):
                continue
            stat = file.stat()
            files.append(FileInfo(
                name = file.relative_to(files_dir).as_posix(),
                size = stat.st_size,
                created = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc),
                modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            ))
        files.sort(key=lambda f: f.modified, reverse=True)
        return files


async def save_uploads(
    session: AsyncSession, project_name: str, files: list[UploadFile], user: ServiceUser
) -> list[str]:
    if not files:
        raise HTTPException(status_code=400, detail="No files were provided")
    project = await project_service.get_project_by_name(session, project_name, user)
    agent_key = await get_project_agent_key(session, project_id=project.id, user_id=user.id)
    async with project_agent_pool.get(agent_key) as agent:
        uploads_dir = agent.uploads_dir
        uploads_dir.mkdir(parents=True, exist_ok=True)

        saved: list[str] = []
        for file in files:
            contents = await file.read()
            if len(contents) > settings.max_upload_size:
                raise HTTPException(status_code=400,
                    detail=f"File '{file.filename}' exceeds the {settings.max_upload_size.human_readable()} upload limit.",
                )
            file_path = uploads_dir / _sanitize_filename(file.filename)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path = write_file_unique(file_path, contents)
            saved.append(file_path.relative_to(uploads_dir).as_posix())

        return saved


async def get_file_path(
    session: AsyncSession, project_name: str, kind: FileKind, name: str, user: ServiceUser
) -> Path:
    """ Resolve a single file's path, raising 404 if it is missing or invalid. """
    project = await project_service.get_project_by_name(session, project_name, user)
    agent_key = await get_project_agent_key(session, project_id=project.id, user_id=user.id)
    async with project_agent_pool.get(agent_key) as agent:
        return _get_file(_kind_dir(agent, kind), name)


async def delete_file(
    session: AsyncSession, project_name: str, kind: FileKind, name: str, user: ServiceUser
) -> None:
    project = await project_service.get_project_by_name(session, project_name, user)
    agent_key = await get_project_agent_key(session, project_id=project.id, user_id=user.id)
    async with project_agent_pool.get(agent_key) as agent:
        files_dir = _kind_dir(agent, kind)
        path = _get_file(files_dir, name)
        path.unlink()
        # Prune any parent directories left empty by the deletion, up to (not including) files_dir.
        for parent in path.parents:
            if parent == files_dir or not path_is_under(files_dir, parent):
                break
            try:
                parent.rmdir()
            except OSError:
                break
