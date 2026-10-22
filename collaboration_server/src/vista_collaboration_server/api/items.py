import uuid
from typing import Annotated as A

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from ..config import CollabSettings
from ..db.db import SessionDep
from ..db.schemas import (
    KIND_PATTERN,
    BlobPublic,
    ItemCreate,
    ItemPublic,
    VersionCreate,
    VersionPublic,
)
from ..services import blobs, items
from ..services.identity import IdentityDep

# Writes require an identity (`IdentityDep`); reads need none until access
# control exists, which will be decided in `services/identity.py`.
router = APIRouter(tags=["items"])


def get_settings(request: Request) -> CollabSettings:
    return request.app.state.settings


SettingsDep = A[CollabSettings, Depends(get_settings)]


def _require_blob(settings: CollabSettings, blob_ref: str | None) -> None:
    if blob_ref is not None and not blobs.exists(settings.blobs_dir, blob_ref):
        raise HTTPException(
            status_code=422,
            detail=f"No blob {blob_ref} has been uploaded; upload it first.",
        )


@router.get("/items")
async def list_items(
    session: SessionDep, kind: A[str | None, Query(pattern=KIND_PATTERN)] = None
) -> list[ItemPublic]:
    """Every item, oldest first; with `kind`, only items of that kind."""
    return [ItemPublic.model_validate(i) for i in await items.list_items(session, kind)]


@router.post("/items", status_code=201)
async def create_item(
    data: ItemCreate, session: SessionDep, identity: IdentityDep, settings: SettingsDep
) -> ItemPublic:
    """A new item, created with its version 1 from `manifest` and `blob_ref`."""
    _require_blob(settings, data.blob_ref)
    return ItemPublic.model_validate(await items.create_item(session, data, identity))


@router.get("/items/{item_id}")
async def get_item(item_id: uuid.UUID, session: SessionDep) -> ItemPublic:
    return ItemPublic.model_validate(await items.get_item(session, item_id))


@router.get("/items/{item_id}/versions")
async def list_versions(item_id: uuid.UUID, session: SessionDep) -> list[VersionPublic]:
    return [
        VersionPublic.model_validate(v)
        for v in await items.list_versions(session, item_id)
    ]


@router.post("/items/{item_id}/versions", status_code=201)
async def publish_version(
    item_id: uuid.UUID,
    data: VersionCreate,
    session: SessionDep,
    identity: IdentityDep,
    settings: SettingsDep,
) -> VersionPublic:
    """
    Publish the item's next version on top of `parent_version`. `409` with the
    current `head_version` when that is no longer the head.
    """
    if data.blob_ref is not None and not blobs.exists(
        settings.blobs_dir, data.blob_ref
    ):
        await items.get_item(session, item_id)  # an unknown item is 404, not 422
        _require_blob(settings, data.blob_ref)
    row = await items.publish(session, item_id, data, identity)
    return VersionPublic.model_validate(row)


@router.get("/items/{item_id}/versions/{version}")
async def get_version(
    item_id: uuid.UUID, version: int, session: SessionDep
) -> VersionPublic:
    return VersionPublic.model_validate(
        await items.get_version(session, item_id, version)
    )


@router.get("/items/{item_id}/versions/{version}/blob")
async def get_version_blob(
    item_id: uuid.UUID, version: int, session: SessionDep, settings: SettingsDep
) -> FileResponse:
    row = await items.get_version(session, item_id, version)
    if row.blob_ref is None:
        raise HTTPException(status_code=404, detail="This version has no blob.")
    path = blobs.path_of(settings.blobs_dir, row.blob_ref)
    if not path.is_file():
        raise HTTPException(
            status_code=500, detail="This version's blob is missing from the store."
        )
    return FileResponse(path, media_type="application/octet-stream")


@router.post("/blobs", status_code=201)
async def upload_blob(
    file: A[UploadFile, File()], identity: IdentityDep, settings: SettingsDep
) -> BlobPublic:
    """
    Store a blob, to be named by a version published after it. Returns its
    reference, the SHA-256 of its bytes.
    """
    ref, size = await blobs.store(file, settings.blobs_dir, settings.max_blob_size)
    return BlobPublic(blob_ref=ref, size=size)
