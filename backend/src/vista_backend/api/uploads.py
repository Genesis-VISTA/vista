import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from ..config import settings
from ..db.db import SessionDep
from ..services import project as project_service
from ..utils.misc import write_file_unique, path_is_under
from ..services.project_agent import project_agent_pool
from .auth import UserDep


router = APIRouter()


def _sanitize_filename(name: str | None) -> str:
    if not name or name in [".", ".."]:
        name = "upload.bin"
    return re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).name)


def _get_upload(uploads_dir: Path, name: str) -> Path:
    """ Return upload path, checks for path traversal etc. and that it exists. """
    if _sanitize_filename(name) != name:
        raise HTTPException(status_code=404, detail="Not found")
    path = uploads_dir / name
    if not path_is_under(uploads_dir, path) or not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return path


class UploadInfo(BaseModel):
    name: str
    size: int
    created: datetime
    modified: datetime


@router.get("/projects/{project_name}/uploads")
async def list_uploads(project_name: str, session: SessionDep, user: UserDep) -> list[UploadInfo]:
    project = await project_service.get_project_by_name(session, project_name)
    async with project_agent_pool.get((project.id, user.id)) as agent:
        uploads_dir = agent.uploads_dir
        if not uploads_dir.exists():
            return []

        files: list[UploadInfo] = []
        for file in uploads_dir.iterdir():
            if not file.is_file():
                continue
            stat = file.stat()
            files.append(UploadInfo(
                name = file.name,
                size = stat.st_size,
                created = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc),
                modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            ))
        files.sort(key=lambda f: f.modified, reverse=True)
        return files


@router.post("/projects/{project_name}/uploads")
async def upload_files(
    project_name: str, files: list[UploadFile], session: SessionDep, user: UserDep,
) -> list[str]:
    if not files:
        raise HTTPException(status_code=400, detail="No files were provided")
    project = await project_service.get_project_by_name(session, project_name)
    async with project_agent_pool.get((project.id, user.id)) as agent:
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
            file_path = write_file_unique(file_path, contents)
            saved.append(file_path.name)

        return saved


@router.get("/projects/{project_name}/uploads/{name}")
async def download_upload(
    project_name: str, name: str, session: SessionDep, user: UserDep,
) -> Response:
    project = await project_service.get_project_by_name(session, project_name)
    async with project_agent_pool.get((project.id, user.id)) as agent:
        path = _get_upload(agent.uploads_dir, name)
    return FileResponse(path, filename=path.name, content_disposition_type="attachment")


@router.delete("/projects/{project_name}/uploads/{name}")
async def delete_upload(
    project_name: str, name: str, session: SessionDep, user: UserDep,
):
    project = await project_service.get_project_by_name(session, project_name)
    async with project_agent_pool.get((project.id, user.id)) as agent:
        path = _get_upload(agent.uploads_dir, name)
    path.unlink()
    return {"ok": True}
