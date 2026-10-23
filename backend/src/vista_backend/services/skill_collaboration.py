"""
Skills on the collaboration server: publishing one, listing the server's, and
importing one.

A skill is an item of kind `skill`. Each version holds the skill's folder as a
gzipped tar (the blob) and a manifest read from its SKILL.md frontmatter, so a
listing can say what a skill is without downloading it. The manifest is the
publisher's word and is only shown: an import reads the unpacked files again,
through the folder-upload path (`import_skill_from_files`), so a skill from a
server meets exactly the rules and limits of a folder a researcher picked.

The local row records where its files came from (`origin_server`, `remote_id`,
`remote_version`) and whether they have changed since (`modified`). A publish
is a compare-and-set on `remote_version`; when the server's head has moved on,
the researcher chooses: take the server's version (`import_skill` with
`replace_modified`), publish on top of the head (`on_top_of`), or publish as a
new skill (`as_new`). Importing is the researcher's own trust decision; nothing
here reviews what a skill does.
"""

import asyncio
import shutil
import tarfile
import uuid
import zlib
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError, field_validator
from sqlalchemy.exc import IntegrityError
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.skill_import import (
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_FILES,
    SkillImportError,
    import_skill_from_files,
    plain_files,
)
from ..agents.skill_repo import SkillRepoError, rename_skill
from ..agents.skills import (
    Skill,
    SkillError,
    SkillMetadata,
    read_skill,
    skill_hpc_jobs,
)
from ..config import settings
from ..db.schemas import LocalSettingsPublic, ProjectTable, SkillTable
from ..utils.misc import now_iso
from ._helpers import new_storage_path
from .collaboration import (
    CollaborationError,
    CollaborationRefused,
    RemoteVersion,
    SharingConflict,
    VersionConflict,
    client_for,
    list_with_heads,
    server_url,
)
from .skills import (
    build_skill_row,
    commit_skill_files,
    get_skill,
    get_skill_optional,
    is_name_clash,
    load_into,
    replace_files,
    work_dir,
)

SKILL_KIND = "skill"
MANIFEST_FORMAT = 1
""" Bumped when a manifest or the archive's layout changes meaning. """
ARCHIVE_NAME = "skill.tar.gz"
MAX_ARCHIVE_BYTES = MAX_UPLOAD_BYTES + 16 * 1024 * 1024
"""
The most a download may be: the files an import takes, plus room for the tar
headers of as many of them as it takes. Checked while streaming, so a server
cannot fill the disk before the import's own limits are reached.
"""


class SkillConflict(SharingConflict):
    """A skill's publish or import that needs the researcher's choice first."""

    def __init__(
        self,
        detail: str,
        code: Literal["version_conflict", "remote_missing", "modified", "name_taken"],
        **extra: Any,
    ):
        super().__init__(detail, code, **extra)


class SkillManifest(SkillMetadata):
    """
    What a version says about the skill in it: its SKILL.md frontmatter, as the
    publisher had it, and what its folder held. `name` is the publisher's name
    for it; an import names a new skill after the item instead.
    """

    format: int = MANIFEST_FORMAT
    hpc_jobs: list[str] = []
    """ The folders under `hpc_jobs/` when published; the import reads its own. """
    files: int = 0
    size: int = 0
    """ Total bytes of the files, unpacked. """
    repo_url: str | None = None
    """ The repository the publisher imported it from, if any. """

    @field_validator("repo_url", mode="before")
    @classmethod
    def _http_only(cls, v):
        """Only an http(s) address becomes a link here; anything else is dropped."""
        if isinstance(v, str) and v.lower().startswith(("https://", "http://")):
            return v
        return None


class LocalCopy(BaseModel):
    """The skill here that came from, or was published as, a server's item."""

    name: str
    remote_version: int | None
    modified: bool


class RemoteSkill(BaseModel):
    """A skill on the collaboration server, as the Skill Hub lists it."""

    id: uuid.UUID
    name: str
    head_version: int
    created_by_name: str | None
    """ Who published its version 1. """
    created_at: str
    published_by_name: str | None = None
    """ Who published its head; None when the head was not fetched. """
    published_at: str | None = None
    manifest: SkillManifest | None = None
    """
    The head's manifest; None when it was not fetched, could not be, or is not
    one this VISTA can read.
    """
    local: LocalCopy | None
    """ This installation's copy of it, if any. """
    name_taken: bool
    """ A different skill here has its name, so importing it needs a new one. """


# ---------------------------------------------------------------------------
# The archive
# ---------------------------------------------------------------------------


def pack_skill(folder: Path, archive: Path) -> tuple[int, int]:
    """
    Write the skill in `folder` to `archive` as a gzipped tar, with paths
    relative to the folder. Refuses what an import would refuse: symlinks,
    special files, and more files or bytes than a folder upload takes.
    Returns the number of files and their total size. Blocking.
    """
    files = sorted(plain_files(folder))
    size = sum(path.stat().st_size for _, path in files)
    if size > MAX_UPLOAD_BYTES:
        raise SkillImportError(
            f"The skill's files come to more than "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB, more than an import takes."
        )
    with tarfile.open(archive, "w:gz") as tar:
        for rel, path in files:
            info = tar.gettarinfo(path, arcname=rel)
            # Nothing about the publisher's machine goes with the files.
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            info.mode = 0o755 if info.mode & 0o111 else 0o644
            with path.open("rb") as f:
                tar.addfile(info, f)
    return len(files), size


def unpack_skill(archive: Path, dest: Path) -> Skill:
    """
    Unpack a skill's archive into `dest` (which must not exist) through
    `import_skill_from_files`, with its checks on paths, counts and sizes.
    Anything in it but files and folders is refused. Raises
    `SkillImportError`. Blocking.
    """
    try:
        tar = tarfile.open(archive, "r:gz")
    except (tarfile.TarError, OSError, EOFError, zlib.error) as e:
        raise SkillImportError(f"The skill's archive cannot be read: {e}") from e
    with tar:
        try:
            members: list[tarfile.TarInfo] = []
            for member in tar:
                if member.isdir():
                    continue
                if not member.isfile():
                    raise SkillImportError(
                        f"{member.name} in the skill's archive is a link or a "
                        "special file; a skill may hold only files and folders."
                    )
                members.append(member)
                if len(members) > MAX_UPLOAD_FILES:
                    raise SkillImportError(
                        f"The skill's archive holds more than {MAX_UPLOAD_FILES} files."
                    )
            entries = []
            for member in members:
                stream = tar.extractfile(member)
                assert stream is not None  # a regular file always has one
                entries.append((member.name, stream))
            executable = {m.name for m in members if m.mode & 0o111}
            return import_skill_from_files(entries, dest, executable=executable)
        except (tarfile.TarError, EOFError, zlib.error) as e:
            raise SkillImportError(f"The skill's archive is damaged: {e}") from e


def _pack_for_publish(row: SkillTable, archive: Path) -> dict[str, Any]:
    """Pack the row's folder into `archive` and return its manifest. Blocking."""
    folder = settings.data_dir / row.path
    try:
        skill = read_skill(folder)
        files, size = pack_skill(folder, archive)
    except (SkillError, ValidationError, SkillImportError) as e:
        raise HTTPException(
            status_code=400, detail=f"{row.name} cannot be published: {e}"
        ) from e
    return SkillManifest(
        **skill.model_dump(exclude={"body"}),
        hpc_jobs=skill_hpc_jobs(folder),
        files=files,
        size=size,
        repo_url=row.repo_url,
    ).model_dump(mode="json")


def _stage(archive: Path, staged: Path, name: str) -> Skill:
    """
    Unpack a downloaded archive, already checked against its version's digest
    by `download_blob`, into `staged` and name the skill `name`. Blocking.
    """
    try:
        skill = unpack_skill(archive, staged)
        if skill.name != name:
            rename_skill(staged, name)
            skill = read_skill(staged)
    except (SkillImportError, SkillRepoError) as e:
        raise HTTPException(status_code=400, detail=f"Cannot import {name}: {e}")
    return skill


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def _manifest(version: RemoteVersion) -> SkillManifest | None:
    try:
        return SkillManifest.model_validate(version.manifest)
    except ValidationError:
        return None


async def list_remote_skills(
    session: AsyncSession, local: LocalSettingsPublic, *, manifests: bool = True
) -> list[RemoteSkill]:
    """
    The server's skills, each with any copy here and, with `manifests`, what
    its head says about it. Without, the listing is one request: enough for
    the head version of each.

    The heads are asked for one by one (`list_with_heads`, design D2): the
    server's listing carries no manifests. A head that cannot be fetched
    leaves its skill without a manifest rather than failing the listing.
    """
    url, _ = server_url(local)
    listed_items = await list_with_heads(local, SKILL_KIND, heads=manifests)

    rows = list((await session.exec(select(SkillTable))).all())
    linked = {r.remote_id: r for r in rows if r.remote_id and r.origin_server == url}
    names = {r.name for r in rows}
    listed = []
    for item, fetched in listed_items:
        copy = linked.get(item.id)
        listed.append(
            RemoteSkill(
                id=item.id,
                name=item.name,
                head_version=item.head_version,
                created_by_name=item.created_by_name,
                created_at=item.created_at,
                published_by_name=fetched.created_by_name if fetched else None,
                published_at=fetched.created_at if fetched else None,
                manifest=_manifest(fetched) if fetched else None,
                local=LocalCopy(
                    name=copy.name,
                    remote_version=copy.remote_version,
                    modified=copy.modified,
                )
                if copy
                else None,
                name_taken=copy is None and item.name in names,
            )
        )
    return listed


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------


async def publish_skill(
    session: AsyncSession,
    local: LocalSettingsPublic,
    name: str,
    *,
    on_top_of: int | None = None,
    as_new: bool = False,
) -> SkillTable:
    """
    Publish the skill's files as the next version of its item on the server in
    effect, or as a new item if it has none there (or `as_new`).

    The next version is built on `remote_version`, or on `on_top_of`, a head
    the researcher has seen and chosen to publish over. When the head has
    moved past it, raises `SkillConflict` ("version_conflict", with the
    head) and changes nothing here. A skill already published there with no
    change since is returned as it is, unless `on_top_of` or `as_new` asks for
    a version anyway.

    The archive is uploaded before the compare-and-set: blobs are
    content-addressed, so one a lost publish leaves behind is reused by its
    retry. A write to the skill while it is published leaves it modified.
    """
    row = await get_skill(session, name)
    if row.bundled:
        raise HTTPException(
            status_code=409,
            detail=f"{name} is bundled with VISTA, so every installation has it "
            "already; it is not published.",
        )
    url, _ = server_url(local)
    linked = row.remote_id is not None and row.origin_server == url and not as_new
    if linked and not row.modified and on_top_of is None:
        return row
    edited_at = row.updated_at
    async with client_for(local) as client, work_dir() as tmp:
        archive = tmp / ARCHIVE_NAME
        manifest = await asyncio.to_thread(_pack_for_publish, row, archive)
        blob_ref = await client.upload_blob(archive)
        if linked:
            assert row.remote_id is not None
            parent = on_top_of if on_top_of is not None else row.remote_version
            assert parent is not None  # set with `remote_id`
            try:
                version = await client.publish_version(
                    row.remote_id,
                    parent_version=parent,
                    manifest=manifest,
                    blob_ref=blob_ref,
                )
            except VersionConflict as e:
                raise SkillConflict(
                    f"{name} has been published since version {parent}: the "
                    f"server's newest is version {e.head_version}.",
                    "version_conflict",
                    head_version=e.head_version,
                    remote_version=row.remote_version,
                ) from e
            except CollaborationRefused as e:
                if e.status_code != 404:
                    raise
                raise SkillConflict(
                    f"{name} is no longer on the collaboration server at {url}; "
                    "publish it as a new skill.",
                    "remote_missing",
                ) from e
            remote_id, remote_version = row.remote_id, version.version
        else:
            item = await client.create_item(
                SKILL_KIND, name, manifest=manifest, blob_ref=blob_ref
            )
            remote_id, remote_version = item.id, item.head_version
    await reread(session, row)
    row.origin_server = url
    row.remote_id = remote_id
    row.remote_version = remote_version
    row.modified = row.updated_at != edited_at
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def reread(session: AsyncSession, row: SQLModel) -> None:
    """
    Read `row` again in a new transaction, so a write another request committed
    while this one was talking to the server is seen. Nothing may be pending.
    """
    await session.rollback()
    await session.refresh(row)


# ---------------------------------------------------------------------------
# Importing
# ---------------------------------------------------------------------------


ImportStatus = Literal["imported", "updated", "unchanged"]


async def linked_skill(
    session: AsyncSession, url: str | None, item_id: uuid.UUID
) -> SkillTable | None:
    """The skill here published as, or imported from, item `item_id` on `url`."""
    return (
        await session.exec(
            select(SkillTable).where(
                SkillTable.origin_server == url, SkillTable.remote_id == item_id
            )
        )
    ).first()


def _name_taken(name: str) -> SkillConflict:
    return SkillConflict(
        f"A skill named {name} is already here. Choose a new name to import this "
        "one as.",
        "name_taken",
        name=name,
    )


def _changed_here(row: SkillTable, number: int) -> SkillConflict:
    return SkillConflict(
        f"{row.name} has changes made here since version {row.remote_version}; "
        f"importing version {number} replaces them.",
        "modified",
        name=row.name,
    )


async def import_skill(
    session: AsyncSession,
    local: LocalSettingsPublic,
    item_id: uuid.UUID,
    *,
    version: int | None = None,
    rename_to: str | None = None,
    replace_modified: bool = False,
    project: ProjectTable | None = None,
) -> tuple[SkillTable, ImportStatus]:
    """
    Import a version (by default the head) of a skill on the server in effect,
    loading it into `project` if given.

    The skill already here from the same item is updated in place, keeping its
    name; if it has local changes, that needs `replace_modified`, else
    `SkillConflict` ("modified"), as does a change made while downloading.
    Otherwise a new skill is made, named after the item or `rename_to`; a name
    already in use raises `SkillConflict` ("name_taken") for the researcher to
    choose another.
    """
    url, _ = server_url(local)
    async with client_for(local) as client:
        item = await client.get_item(item_id)
        if item.kind != SKILL_KIND:
            raise HTTPException(
                status_code=400,
                detail=f"{item.name} on the collaboration server is a {item.kind}, "
                "not a skill.",
            )
        number = version if version is not None else item.head_version
        remote = await client.get_version(item_id, number)
        if remote.blob_ref is None:
            raise CollaborationError(
                f"Version {number} of {item.name} on the collaboration server "
                "has no files."
            )
        manifest = _manifest(remote)

        existing = await linked_skill(session, url, item_id)
        if existing is not None:
            if existing.remote_version == number and not existing.modified:
                if project is not None:
                    load_into(session, project, existing.name)
                    await session.commit()
                    await session.refresh(existing)
                return existing, "unchanged"
            if existing.modified and not replace_modified:
                raise _changed_here(existing, number)
            name, edited_at = existing.name, existing.updated_at
        else:
            name = (rename_to or "").strip() or item.name
            if await get_skill_optional(session, name) is not None:
                raise _name_taken(name)

        async with work_dir() as tmp:
            archive = tmp / ARCHIVE_NAME
            await client.download_blob(
                item_id,
                number,
                archive,
                blob_ref=remote.blob_ref,
                max_bytes=MAX_ARCHIVE_BYTES,
            )
            staged = tmp / "skill"
            skill = await asyncio.to_thread(_stage, archive, staged, name)
            path = new_storage_path()
            dest = settings.data_dir / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copytree, staged, dest)

    repo_url = manifest.repo_url if manifest else None
    old_path: str | None = None
    if existing is None:
        row = build_skill_row(skill, path=path, repo_url=repo_url, now=now_iso())
        row.origin_server = url
        row.remote_id = item_id
        status: ImportStatus = "imported"
    else:
        await reread(session, existing)
        if existing.updated_at != edited_at:
            await asyncio.to_thread(shutil.rmtree, dest, ignore_errors=True)
            raise _changed_here(existing, number)
        row = existing
        old_path = replace_files(row, skill, path)
        row.repo_url = repo_url
        status = "updated"
    row.remote_version = number
    row.modified = False
    try:
        await commit_skill_files(session, row, old_path=old_path, project=project)
    except IntegrityError as e:
        if is_name_clash(e):
            raise _name_taken(name) from e
        raise
    return row, status
