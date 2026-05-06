import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from ..config import settings
from ..utils.misc import write_file_unique, path_is_under


router = APIRouter()



def _sanitize_filename(name: str | None) -> str:
    name = name or "upload.bin"
    return re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).name)

def _get_upload(name: str):
    """ Return upload path, checks for path traversal etc. and that it exists. """
    if _sanitize_filename(name) != name:
        raise HTTPException(status_code=404, detail="Not found")
    path = settings.uploads_dir / name
    if not path_is_under(settings.uploads_dir, path) or not path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return path


class UploadInfo(BaseModel):
    name: str
    size: int
    created: datetime
    modified: datetime

@router.get("/uploads")
async def list_uploads() -> list[UploadInfo]:
    uploads_dir = settings.uploads_dir
    uploads_dir.mkdir(parents=True, exist_ok=True)

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


@router.post("/uploads")
async def upload_files(files: list[UploadFile]) -> list[str]:
    if not files:
        raise HTTPException(status_code=400, detail="No files were provided")
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []
    for file in files:
        contents = await file.read()
        if len(contents) > settings.max_upload_size:
            raise HTTPException(status_code=400,
                detail=f"File '{file.filename}' exceeds the 20MB upload limit.",
            )
        file_path = settings.uploads_dir / _sanitize_filename(file.filename)
        file_path = write_file_unique(file_path, contents)
        saved.append(str(file_path))

    return saved


@router.get("/uploads/{name}")
async def download_upload(name: str) -> Response:
    path = _get_upload(name)
    return FileResponse(
        path,
        filename=path.name,
    )


@router.delete("/uploads/{name}")
async def delete_upload(name: str):
    path = _get_upload(name)
    path.unlink()
    return {"ok": True}

