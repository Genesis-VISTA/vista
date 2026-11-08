from fastapi import APIRouter, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.convertors import Convertor, register_url_convertor

from ..db.db import SessionDep
from ..services import files as files_service
from ..services.files import FileInfo, FileKind


router = APIRouter()

# Types a browser may show inline, decided by extension alone. HTML and SVG are
# deliberately absent: they are active content and are only ever downloaded.
INLINE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "pdf"})

# Sent with every file response: no sniffing, and a CSP that sandboxes the document.
FILE_SAFETY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "sandbox; default-src 'none'",
}


class FileKindConvertor(Convertor):
    """
    Just using the FileKind literal causes Starlette to capture all /project/{project_name}/* routes
    in the project /project/{project_name}/{kind} routes. A custom converter fixes that.
    """

    regex = "uploads|outputs"

    def convert(self, value: str):
        return value

    def to_string(self, value: str):
        return value


register_url_convertor("file_kind", FileKindConvertor())


@router.get("/projects/{project_name}/{kind:file_kind}")
async def list_files(
    project_name: str,
    kind: FileKind,
    session: SessionDep,
) -> list[FileInfo]:
    return await files_service.list_files(session, project_name, kind)


@router.post("/projects/{project_name}/uploads")
async def upload_files(
    project_name: str,
    files: list[UploadFile],
    session: SessionDep,
) -> list[str]:
    return await files_service.save_uploads(session, project_name, files)


@router.get("/projects/{project_name}/{kind:file_kind}/{name:path}")
async def download_file(
    project_name: str,
    kind: FileKind,
    name: str,
    session: SessionDep,
    preview: bool = False,
) -> Response:
    path = await files_service.get_file_path(session, project_name, kind, name)
    extension = path.suffix.lstrip(".").lower()
    inline = preview and extension in INLINE_EXTENSIONS
    return FileResponse(
        path,
        filename=path.name,
        content_disposition_type="inline" if inline else "attachment",
        headers=FILE_SAFETY_HEADERS,
    )


@router.delete("/projects/{project_name}/{kind:file_kind}/{name:path}")
async def delete_file(
    project_name: str,
    kind: FileKind,
    name: str,
    session: SessionDep,
):
    await files_service.delete_file(session, project_name, kind, name)
    return {"ok": True}
