import uuid
from typing import Annotated as A

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from ..config import CollabSettings
from ..db.db import SessionDep
from ..db.schemas import (
    KIND_PATTERN,
    BlobPublic,
    ItemCopy,
    ItemCreate,
    ItemPublic,
    VersionCreate,
    VersionPublic,
)
from ..services import blob_access, blobs, items
from ..services.identity import UserDep, get_user

# Every route needs a signed-in user (`services/identity.py`), reads included,
# and sees only the items in their teams (`services/teams.py`); anything else
# is 404, as if it did not exist. Writes record who made them. Blobs are
# uploaded by anyone signed in, read only through a version they can see, and
# named in a version only by their uploader or someone who sees one naming them.
router = APIRouter(tags=["items"], dependencies=[Depends(get_user)])


def get_settings(request: Request) -> CollabSettings:
    return request.app.state.settings


SettingsDep = A[CollabSettings, Depends(get_settings)]


async def _require_blob(
    session: SessionDep, settings: CollabSettings, blob_ref: str | None, user
) -> None:
    """
    Refuse a blob that is not stored, or that the caller may not name
    (`blob_access.may_use`), alike. Ends the read transaction it starts, so a
    publish after it opens its own with its compare-and-set.
    """
    if blob_ref is None:
        return
    allowed = blobs.exists(settings.blobs_dir, blob_ref) and await blob_access.may_use(
        session, blob_ref, user
    )
    await session.rollback()
    if not allowed:
        raise HTTPException(
            status_code=422,
            detail=f"No blob {blob_ref} has been uploaded; upload it first.",
        )


@router.get("/items")
async def list_items(
    session: SessionDep,
    user: UserDep,
    kind: A[str | None, Query(pattern=KIND_PATTERN)] = None,
    team_id: uuid.UUID | None = None,
) -> list[ItemPublic]:
    """
    Every item in the caller's teams, oldest first; with `kind`, only items of
    that kind, and with `team_id`, only that team's.
    """
    rows = await items.list_items(session, user, kind, team_id)
    return [ItemPublic.model_validate(i) for i in rows]


@router.post("/items", status_code=201)
async def create_item(
    data: ItemCreate, session: SessionDep, user: UserDep, settings: SettingsDep
) -> ItemPublic:
    """
    A new item in `team_id`, a team the caller is in, created with its version
    1 from `manifest` and `blob_ref`. `404` for a team they are not in.
    """
    await _require_blob(session, settings, data.blob_ref, user)
    return ItemPublic.model_validate(await items.create_item(session, data, user))


@router.get("/items/{item_id}")
async def get_item(
    item_id: uuid.UUID, session: SessionDep, user: UserDep
) -> ItemPublic:
    return ItemPublic.model_validate(await items.get_item(session, item_id, user))


@router.post("/items/{item_id}/copy", status_code=201)
async def copy_item(
    item_id: uuid.UUID, data: ItemCopy, session: SessionDep, user: UserDep
) -> ItemPublic:
    """
    Copy the item's newest version into `team_id`, a team the caller is in, as
    a new item (its `copied_from` names the original). The original is left as
    it is. `404` for an item or team the caller is not in.
    """
    return ItemPublic.model_validate(
        await items.copy_item(session, item_id, data, user)
    )


@router.get("/items/{item_id}/versions")
async def list_versions(
    item_id: uuid.UUID, session: SessionDep, user: UserDep
) -> list[VersionPublic]:
    return [
        VersionPublic.model_validate(v)
        for v in await items.list_versions(session, item_id, user)
    ]


@router.post("/items/{item_id}/versions", status_code=201)
async def publish_version(
    item_id: uuid.UUID,
    data: VersionCreate,
    session: SessionDep,
    user: UserDep,
    settings: SettingsDep,
) -> VersionPublic:
    """
    Publish the item's next version on top of `parent_version`. `409` with the
    current `head_version` when that is no longer the head.
    """
    if data.blob_ref is not None:
        await items.get_item(session, item_id, user)  # an unknown item is 404, not 422
        await _require_blob(session, settings, data.blob_ref, user)
    row = await items.publish(session, item_id, data, user)
    return VersionPublic.model_validate(row)


@router.get("/items/{item_id}/versions/{version}")
async def get_version(
    item_id: uuid.UUID, version: int, session: SessionDep, user: UserDep
) -> VersionPublic:
    return VersionPublic.model_validate(
        await items.get_version(session, item_id, version, user)
    )


@router.get("/items/{item_id}/versions/{version}/blob")
async def get_version_blob(
    item_id: uuid.UUID,
    version: int,
    session: SessionDep,
    user: UserDep,
    settings: SettingsDep,
) -> FileResponse:
    row = await items.get_version(session, item_id, version, user)
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
    file: A[UploadFile, File()],
    session: SessionDep,
    user: UserDep,
    settings: SettingsDep,
) -> BlobPublic:
    """
    Store a blob, to be named by a version published after it. Returns its
    reference, the SHA-256 of its bytes.
    """
    ref, size = await blobs.store(file, settings.blobs_dir, settings.max_blob_size)
    await blob_access.record_upload(session, ref, user)
    return BlobPublic(blob_ref=ref, size=size)


@router.post("/blobs/stream", status_code=201)
async def upload_blob_stream(
    request: Request, session: SessionDep, user: UserDep, settings: SettingsDep
) -> BlobPublic:
    """
    Store a blob sent as the raw request body, for one too large for a
    multipart `POST /blobs` (a knowledge base). It is written to disk as it
    arrives, never held whole. Returns its reference, the SHA-256 of its bytes.
    """
    ref, size = await blobs.store_stream(
        request.stream(), settings.blobs_dir, settings.max_blob_size
    )
    await blob_access.record_upload(session, ref, user)
    return BlobPublic(blob_ref=ref, size=size)
