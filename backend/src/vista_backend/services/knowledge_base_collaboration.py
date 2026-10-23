"""
Knowledge bases on the collaboration server: publishing one, listing the
server's, and importing one.

A knowledge base is an item of kind `knowledge-base`, named by its slug. Each
version's blob is a tar of its files (`pdfs/`: PDFs, tables and notes, every
format `indexer.ACCEPTED_SUFFIXES` names) and its built index (`rag_db/`),
so an importer searches it at once without indexing anything: indexing stays
local, and needs the publisher's embedding model. The manifest says what the
version is (name, description, the publications' metadata) and what built the
index (embedding model and revision, chromadb version), so an importer whose
index would not be readable or comparable refuses it before downloading.

The index is snapshotted, not copied: its SQLite database through SQLite's
backup API while the store's indexing lock is held (`indexer.snapshot_store`),
so a publish never ships half of an indexing run. A publish is refused while
the knowledge base is indexing. A knowledge base with no index yet (nothing
indexed) is published without one. The archive goes up streamed, as the raw
body of one request (`upload_large_blob`), as it can run to gigabytes.

Publishing and importing a knowledge base hold its slug's lock (`kb_lock`), so
two imports, or an import and a publish, never interleave on one folder.

An import never writes over a store: it lands in a new subfolder of the
knowledge base's folder, `CURRENT` there is switched to it, and the MCP server,
which finds stores on disk, reopens the new one on its next search. The old
store is then removed if it can be (on Windows not while it is open), or on a
later import or the knowledge base's deletion.

The local row records where it came from and whether it changed since, as a
skill's does; conflicts offer the same choices.
"""

import asyncio
import os
import re
import shutil
import tarfile
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, cast

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..agents.skill_import import SkillImportError, clean_relpath
from ..config import settings
from ..db.schemas import (
    KnowledgeBaseBuildStatus,
    KnowledgeBasePublic,
    KnowledgeBaseTable,
    LocalSettingsPublic,
    SLUG_PATTERN,
    Publication,
    is_valid_slug,
)
from ..utils import indexer
from ..utils.misc import now_iso
from .collaboration import (
    CollaborationClient,
    CollaborationError,
    CollaborationRefused,
    RemoteVersion,
    SharingConflict,
    VersionConflict,
    client_for,
    list_with_heads,
    required_url,
    server_url,
)
from .knowledge_base import (
    get_kb,
    get_kb_optional,
    kb_content,
    kb_folder,
    kb_lock,
    read_current,
    write_current,
)
from .skill_collaboration import LocalCopy, reread

KB_KIND = "knowledge-base"
MANIFEST_FORMAT = 1
""" Bumped when a manifest or the archive's layout changes meaning. """
ARCHIVE_NAME = "knowledge-base.tar"
MAX_ARCHIVE_MEMBERS = 100_000
DISK_MARGIN = 256 * 1024 * 1024
""" Free space an import leaves beyond the archive and its unpacked copy. """
INCOMING_DIR = ".incoming"
"""
Under `knowledge_bases_dir`, where publishes and imports do their work: on the
same filesystem as the knowledge bases, so an unpacked import is moved into
place, and named so neither the backend nor the MCP server takes it for one.
"""

SLUG_RULE = (
    "A knowledge base's slug is 1 to 80 lowercase letters, digits and dashes, "
    "starting and ending with a letter or digit."
)


class KnowledgeBaseConflict(SharingConflict):
    """A knowledge base's publish or import that needs the researcher's choice first."""

    def __init__(
        self,
        detail: str,
        code: Literal[
            "version_conflict",
            "remote_missing",
            "modified",
            "name_taken",
            "name_invalid",
        ],
        **extra: Any,
    ):
        super().__init__(detail, code, **extra)


class KnowledgeBaseManifest(BaseModel):
    """
    What a version of a knowledge base is, and what built its index. `slug` is
    the publisher's; an import names a new knowledge base after the item.
    """

    format: int = MANIFEST_FORMAT
    slug: Annotated[str, Field(pattern=SLUG_PATTERN)]
    name: str
    description: str | None = None
    publications: list[Publication] = []
    """ The publisher's entries: each file's citation and indexing state. """
    build_status: KnowledgeBaseBuildStatus = "ready"
    last_built_at: str | None = None
    files: int = 0
    """ The files in the archive. """
    size: int = 0
    """ Their total bytes. """
    archive_size: int = Field(ge=0)
    """ The blob's bytes: a download is refused past them. """
    has_index: bool = True
    """
    The archive holds an index (`rag_db/`). False for a knowledge base nothing
    has been indexed into yet; the importer starts it with an empty one.
    """
    embedding_model: str
    embedding_revision: str
    chromadb: str
    """ The chromadb version that wrote the index. """


class RemoteKnowledgeBase(BaseModel):
    """A knowledge base on the collaboration server, as the import list shows it."""

    id: uuid.UUID
    name: str
    """ The item's name: the publisher's slug. """
    head_version: int
    created_by_name: str | None
    created_at: str
    published_by_name: str | None = None
    published_at: str | None = None
    manifest: KnowledgeBaseManifest | None = None
    """ The head's manifest; None when it was not fetched or is not one this VISTA reads. """
    local: LocalCopy | None
    """ This installation's copy of it, if any; `name` is its slug. """
    name_taken: bool
    """ A different knowledge base here has its slug, so importing it needs a new one. """


PublishStatus = Literal["published", "unchanged"]
ImportStatus = Literal["imported", "updated", "unchanged"]


class KnowledgeBasePublishResult(BaseModel):
    status: PublishStatus
    """ `unchanged`: it is published there already, with no change since. """
    knowledge_base: KnowledgeBasePublic


class KnowledgeBaseImportResult(BaseModel):
    status: ImportStatus
    knowledge_base: KnowledgeBasePublic


def manifest_of(version: RemoteVersion) -> KnowledgeBaseManifest | None:
    """A version's manifest, or None when it is not one this VISTA reads."""
    try:
        manifest = KnowledgeBaseManifest.model_validate(version.manifest)
    except ValidationError:
        return None
    return manifest if manifest.format == MANIFEST_FORMAT else None


def incompatibility(
    manifest: KnowledgeBaseManifest, here: indexer.StoreFormat
) -> str | None:
    """
    Why a version's index cannot be used here, or None when it can (or it
    has none).

    The embedding model and its revision must be the ones this VISTA embeds
    queries with: vectors from another model, or another snapshot of it, are
    not comparable, and nothing would say so. The chromadb version must be
    this one's major version and no newer: chromadb migrates an older store
    forward when it opens it, and cannot read a newer one.
    """
    if not manifest.has_index:
        return None
    if (manifest.embedding_model, manifest.embedding_revision) != (
        here.embedding_model,
        here.embedding_revision,
    ):
        return (
            f"{manifest.name} was indexed with {manifest.embedding_model} at "
            f"{manifest.embedding_revision[:12]}; this VISTA searches with "
            f"{here.embedding_model} at {here.embedding_revision[:12]}, so it "
            "cannot search that index."
        )
    theirs, ours = _version(manifest.chromadb), _version(here.chromadb)
    if theirs is None or ours is None or theirs[0] != ours[0] or theirs > ours:
        return (
            f"{manifest.name}'s index was written by chromadb {manifest.chromadb}, "
            f"which this VISTA's chromadb {here.chromadb} cannot read."
        )
    return None


def _version(text: str) -> tuple[int, int, int] | None:
    """`1.5.9` (or `1.5.9rc1`, `1.5`) as (1, 5, 9); None for anything else."""
    match = re.match(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", text.strip())
    if match is None:
        return None
    major, minor, patch = (int(g) if g else 0 for g in match.groups())
    return major, minor, patch


def is_unchanged_seed(row: KnowledgeBaseTable) -> bool:
    """
    `row` is a seeded knowledge base as VISTA seeded it, so its `seed_item`:
    unchanged since, and published as nothing else.
    """
    return (
        row.seed_item is not None
        and not row.modified
        and row.remote_id in (None, row.seed_item)
        and row.remote_version in (None, 1)
    )


async def _seed_matching(
    session: AsyncSession, item_id: uuid.UUID
) -> KnowledgeBaseTable | None:
    """The unchanged seeded knowledge base here that is item `item_id`, if any."""
    rows = (
        await session.exec(
            select(KnowledgeBaseTable).where(KnowledgeBaseTable.seed_item == item_id)
        )
    ).all()
    return next((row for row in rows if is_unchanged_seed(row)), None)


async def _link_seed(
    session: AsyncSession, row: KnowledgeBaseTable, url: str, item_id: uuid.UUID
) -> KnowledgeBaseTable:
    """Record that seeded `row` is version 1 of `item_id` on `url`, unchanged."""
    row.origin_server = url
    row.remote_id = item_id
    row.remote_version = 1
    row.modified = False
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def linked_knowledge_base(
    session: AsyncSession, url: str | None, item_id: uuid.UUID
) -> KnowledgeBaseTable | None:
    """
    The knowledge base here published as, or imported from, item `item_id` on
    `url`. An unchanged seeded one that is that item (`seed_item`) is linked
    to it now, and is the one.
    """
    row = (
        await session.exec(
            select(KnowledgeBaseTable).where(
                KnowledgeBaseTable.origin_server == url,
                KnowledgeBaseTable.remote_id == item_id,
            )
        )
    ).first()
    if row is None and url is not None:
        seed = await _seed_matching(session, item_id)
        if seed is not None:
            row = await _link_seed(session, seed, url, item_id)
    return row


def require_idle(kb: KnowledgeBaseTable, doing: str) -> None:
    """Refuse `doing` (a verb phrase) while the knowledge base is indexing."""
    if indexer.get_progress(kb.rag_db_path) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"{kb.name} ({kb.slug}) is indexing; {doing} once it has finished.",
        )


def clear_incoming() -> None:
    """Remove what publishes and imports left in `INCOMING_DIR`, as at startup."""
    shutil.rmtree(settings.knowledge_bases_dir / INCOMING_DIR, ignore_errors=True)


@asynccontextmanager
async def _incoming() -> AsyncIterator[Path]:
    """A work folder beside the knowledge bases, removed off the event loop."""
    tmp = settings.knowledge_bases_dir / INCOMING_DIR / uuid.uuid4().hex
    tmp.mkdir(parents=True)
    try:
        yield tmp
    finally:
        await asyncio.to_thread(shutil.rmtree, tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# The archive
# ---------------------------------------------------------------------------


def _files(pdfs_dir: Path) -> list[tuple[str, Path]]:
    """
    Every file under `pdfs_dir` a knowledge base holds (`indexer.ACCEPTED_SUFFIXES`),
    as (path relative to it, path). Links are left out.
    """
    found = []
    if pdfs_dir.is_dir():
        for path in sorted(pdfs_dir.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix.lower() not in indexer.ACCEPTED_SUFFIXES:
                continue
            found.append((path.relative_to(pdfs_dir).as_posix(), path))
    return found


def _add(tar: tarfile.TarFile, path: Path, arcname: str) -> None:
    info = tar.gettarinfo(path, arcname=arcname)
    # Nothing about the publisher's machine goes with the files.
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    info.mode = 0o644
    with path.open("rb") as f:
        tar.addfile(info, f)


def pack_knowledge_base(
    pdfs_dir: Path, store: Path | None, archive: Path
) -> tuple[int, int]:
    """
    Write the files under `pdfs_dir` and the store snapshot `store` (None for
    a knowledge base with no index yet) to `archive`, an uncompressed tar
    (PDFs and vectors hardly compress), as `pdfs/…` and `rag_db/…`. Returns
    the number of files and their total size. Blocking.
    """
    files = _files(pdfs_dir)
    with tarfile.open(archive, "w") as tar:
        for rel, path in files:
            _add(tar, path, f"pdfs/{rel}")
        if store is not None:
            for path in sorted(store.rglob("*")):
                if path.is_file():
                    _add(tar, path, f"rag_db/{path.relative_to(store).as_posix()}")
    return len(files), sum(path.stat().st_size for _, path in files)


class ArchiveError(Exception):
    """An archive this VISTA will not unpack; the message says why."""


def _member_path(member: tarfile.TarInfo) -> PurePosixPath:
    """
    Where a member goes, refusing anything but a file under `pdfs/` or
    `rag_db/`: the skill upload's path rule (`clean_relpath`: no absolute
    path, `..`, backslash, or `:` of a Windows drive), then the two folders.
    """
    try:
        path = clean_relpath(member.name)
    except SkillImportError as e:
        raise ArchiveError(
            f"{member.name} is not a path a knowledge base holds."
        ) from e
    if len(path.parts) < 2 or path.parts[0] not in ("pdfs", "rag_db"):
        raise ArchiveError(f"{member.name} is not a path a knowledge base holds.")
    if not member.isfile():
        raise ArchiveError(
            f"{member.name} is a link or a special file; a knowledge base holds "
            "only files and folders."
        )
    if path.parts[0] == "pdfs" and path.suffix.lower() not in indexer.ACCEPTED_SUFFIXES:
        raise ArchiveError(f"{member.name} is not a file a knowledge base holds.")
    return path


def unpack_knowledge_base(archive: Path, dest: Path, *, has_index: bool) -> None:
    """
    Unpack `archive`, already checked against its version's digest by
    `download_blob`, into `dest`, which must not exist: only files under
    `pdfs/` (`indexer.ACCEPTED_SUFFIXES`) and `rag_db/`, and with `has_index` a store
    (`rag_db/chroma.sqlite3`) among them. `dest` always ends up with both
    folders. Raises `ArchiveError`. Blocking.
    """
    try:
        tar = tarfile.open(archive, "r:")
    except (tarfile.TarError, OSError) as e:
        raise ArchiveError(f"The knowledge base's archive cannot be read: {e}") from e
    dest.mkdir(parents=True)
    root = dest.resolve()
    with tar:
        try:
            count = 0
            for member in tar:
                if member.isdir():
                    continue
                count += 1
                if count > MAX_ARCHIVE_MEMBERS:
                    raise ArchiveError(
                        f"The archive holds more than {MAX_ARCHIVE_MEMBERS} files."
                    )
                target = dest.joinpath(*_member_path(member).parts)
                # Whatever the platform makes of the name, it stays inside.
                if not target.resolve().is_relative_to(root):
                    raise ArchiveError(f"{member.name} would land outside the import.")
                stream = tar.extractfile(member)
                assert stream is not None  # a regular file always has one
                target.parent.mkdir(parents=True, exist_ok=True)
                with stream, target.open("xb") as out:
                    shutil.copyfileobj(stream, out, 1 << 20)
        except FileExistsError as e:
            raise ArchiveError(f"The archive holds {e.filename} twice.") from e
        except (tarfile.TarError, EOFError) as e:
            raise ArchiveError(f"The knowledge base's archive is damaged: {e}") from e
    if has_index and not (dest / "rag_db" / "chroma.sqlite3").is_file():
        raise ArchiveError("The archive holds no index (rag_db/chroma.sqlite3).")
    (dest / "pdfs").mkdir(exist_ok=True)
    (dest / "rag_db").mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


async def list_remote_knowledge_bases(
    session: AsyncSession, local: LocalSettingsPublic, *, manifests: bool = True
) -> list[RemoteKnowledgeBase]:
    """The server's knowledge bases, each with any copy here and, with `manifests`, its head."""
    url, _ = server_url(local)
    listed_items = await list_with_heads(local, KB_KIND, heads=manifests)
    rows = list((await session.exec(select(KnowledgeBaseTable))).all())
    linked = {
        r.remote_id: LocalCopy(
            name=r.slug, remote_version=r.remote_version, modified=r.modified
        )
        for r in rows
        if r.remote_id and r.origin_server == url
    }
    # An unchanged seed is its item already: importing it only links it.
    for r in rows:
        if r.seed_item is not None and is_unchanged_seed(r):
            linked[r.seed_item] = LocalCopy(
                name=r.slug, remote_version=1, modified=False
            )
    slugs = {r.slug for r in rows}
    listed = []
    for item, fetched in listed_items:
        copy = linked.get(item.id)
        listed.append(
            RemoteKnowledgeBase(
                id=item.id,
                name=item.name,
                head_version=item.head_version,
                created_by_name=item.created_by_name,
                created_at=item.created_at,
                published_by_name=fetched.created_by_name if fetched else None,
                published_at=fetched.created_at if fetched else None,
                manifest=manifest_of(fetched) if fetched else None,
                local=copy,
                name_taken=copy is None and item.name in slugs,
            )
        )
    return listed


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------


def _shipped(publications: list[Any]) -> list[Publication]:
    """The entries as an importer gets them: none still waiting for this indexer."""
    shipped = []
    for p in publications:
        pub = Publication.model_validate(p) if isinstance(p, dict) else p.model_copy()
        if pub.index_status in ("queued", "indexing"):
            pub.index_status = "unindexed"
        shipped.append(pub)
    return shipped


async def publish_knowledge_base(
    session: AsyncSession,
    local: LocalSettingsPublic,
    slug: str,
    *,
    on_top_of: int | None = None,
    as_new: bool = False,
) -> KnowledgeBasePublishResult:
    """
    Publish the knowledge base's files and index as the next version of its
    item on the server in effect, or as a new item if it has none there (or
    `as_new`). Refused while it is indexing.

    The next version is built on `remote_version`, or on `on_top_of`, a head
    the researcher has seen and chosen to publish over; a head past it raises
    `KnowledgeBaseConflict` (`version_conflict`, with the head) and changes
    nothing here. One published there with no change since is not published
    again (`unchanged`), unless `on_top_of` or `as_new` asks. A change made
    while it is published leaves it modified.

    An unchanged seed is its `seed_item`: created under that id by the first
    installation to publish it, and only linked by every other. A seed changed
    here is published as a new item, so the seed's own stays as VISTA seeds it.
    """
    url = required_url(local)
    async with kb_lock(slug):
        return await _publish(
            session, local, url=url, slug=slug, on_top_of=on_top_of, as_new=as_new
        )


async def _publish(
    session: AsyncSession,
    local: LocalSettingsPublic,
    *,
    url: str,
    slug: str,
    on_top_of: int | None,
    as_new: bool,
) -> KnowledgeBasePublishResult:
    row = await get_kb(session, slug)
    require_idle(row, "publish it")
    public = KnowledgeBasePublic.model_validate(row)
    linked = row.remote_id is not None and row.origin_server == url and not as_new
    if linked and not row.modified and on_top_of is None:
        return KnowledgeBasePublishResult(status="unchanged", knowledge_base=public)
    if row.remote_id is not None and row.remote_id == row.seed_item:
        # The seed's item stays as VISTA seeds it: changed here, this is new.
        linked = False
    seed_id = row.seed_item if not as_new and is_unchanged_seed(row) else None
    if seed_id is not None:
        # Another installation published this seed already: link it, send nothing.
        async with client_for(local) as client:
            if await _item_exists(client, seed_id):
                return await _seed_published(session, row, url, seed_id)
    before = kb_content(row)
    here = await indexer.store_format()
    # A knowledge base nothing has been indexed into has no store yet.
    has_index = (Path(row.rag_db_path) / "chroma.sqlite3").is_file()
    # The snapshot and the archive are copies of the files and the store, on
    # the disk that holds the database too.
    check_space(
        await asyncio.to_thread(_bytes_to_pack, Path(row.pdfs_dir), row.rag_db_path),
        f"Publishing {row.name}",
    )

    async with client_for(local) as client, _incoming() as tmp:
        store = tmp / "rag_db" if has_index else None
        archive = tmp / ARCHIVE_NAME
        try:
            if store is not None:
                await indexer.snapshot_store(row.rag_db_path, store)
            files, size = await asyncio.to_thread(
                pack_knowledge_base, Path(row.pdfs_dir), store, archive
            )
        except Exception as e:  # sqlite3 and OS errors alike
            raise HTTPException(
                status_code=500, detail=f"{row.name} could not be packed: {e}"
            ) from e
        manifest = KnowledgeBaseManifest(
            slug=public.slug,
            name=public.name,
            description=public.description,
            publications=_shipped(public.publications),
            build_status=public.build_status,
            last_built_at=public.last_built_at,
            files=files,
            size=size,
            archive_size=archive.stat().st_size,
            has_index=has_index,
            embedding_model=here.embedding_model,
            embedding_revision=here.embedding_revision,
            chromadb=here.chromadb,
        ).model_dump(mode="json")
        blob_ref = await client.upload_large_blob(archive)
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
                raise KnowledgeBaseConflict(
                    f"{slug} has been published since version {parent}: the "
                    f"server's newest is version {e.head_version}.",
                    "version_conflict",
                    head_version=e.head_version,
                    remote_version=row.remote_version,
                ) from e
            except CollaborationRefused as e:
                if e.status_code != 404:
                    raise
                raise KnowledgeBaseConflict(
                    f"{slug} is no longer on the collaboration server at {url}; "
                    "publish it as a new knowledge base.",
                    "remote_missing",
                ) from e
            remote_id, remote_version = row.remote_id, version.version
        else:
            try:
                item = await client.create_item(
                    KB_KIND, slug, manifest=manifest, blob_ref=blob_ref, item_id=seed_id
                )
            except CollaborationRefused as e:
                if seed_id is None or e.status_code != 409:
                    raise
                # Another installation published this seed while this one packed.
                return await _seed_published(session, row, url, seed_id)
            remote_id, remote_version = item.id, item.head_version

    await reread(session, row)
    row.origin_server = url
    row.remote_id = remote_id
    row.remote_version = remote_version
    row.modified = kb_content(row) != before
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return KnowledgeBasePublishResult(
        status="published", knowledge_base=KnowledgeBasePublic.model_validate(row)
    )


async def _item_exists(client: CollaborationClient, item_id: uuid.UUID) -> bool:
    try:
        await client.get_item(item_id)
    except CollaborationRefused as e:
        if e.status_code != 404:
            raise
        return False
    return True


async def _seed_published(
    session: AsyncSession, row: KnowledgeBaseTable, url: str, item_id: uuid.UUID
) -> KnowledgeBasePublishResult:
    """A seeded knowledge base found on the server already, linked to it."""
    await reread(session, row)
    row = await _link_seed(session, row, url, item_id)
    return KnowledgeBasePublishResult(
        status="unchanged", knowledge_base=KnowledgeBasePublic.model_validate(row)
    )


# ---------------------------------------------------------------------------
# Importing
# ---------------------------------------------------------------------------


def _name_taken(slug: str) -> KnowledgeBaseConflict:
    return KnowledgeBaseConflict(
        f"A knowledge base with the slug {slug} is already here. Choose a new slug "
        "to import this one as.",
        "name_taken",
        name=slug,
    )


def _changed_here(row: KnowledgeBaseTable, number: int) -> KnowledgeBaseConflict:
    return KnowledgeBaseConflict(
        f"{row.slug} has changes made here since version {row.remote_version}; "
        f"importing version {number} replaces them.",
        "modified",
        name=row.slug,
    )


def _check_slug(slug: str, *, chosen: bool) -> None:
    """
    Refuse a slug no knowledge base can have. `chosen`: the researcher typed
    it; otherwise it is the item's, and they are asked for one (`name_invalid`).
    """
    if is_valid_slug(slug):
        return
    if chosen:
        raise HTTPException(
            status_code=400, detail=f"{slug!r} cannot be used. {SLUG_RULE}"
        )
    raise KnowledgeBaseConflict(
        f"{slug} cannot be a knowledge base's slug here. {SLUG_RULE} Choose a slug "
        "to import it as.",
        "name_invalid",
        name=slug,
    )


def _bytes_to_pack(pdfs_dir: Path, rag_db_path: str) -> int:
    """What a publish packs: the files and the store, in bytes. Blocking."""
    store = Path(rag_db_path)
    stored = sum(p.stat().st_size for p in store.rglob("*") if p.is_file())
    return stored + sum(path.stat().st_size for _, path in _files(pdfs_dir))


def check_space(archive_bytes: int, what: str) -> None:
    """
    Refuse (507) when archives of `archive_bytes` in all, and their copies
    (unpacked on import, the snapshot on publish), would not fit beside the
    knowledge bases. `what` begins the refusal ("Importing salt-papers").
    """
    root = settings.knowledge_bases_dir
    root.mkdir(parents=True, exist_ok=True)
    needed = 2 * archive_bytes + DISK_MARGIN
    free = shutil.disk_usage(root).free
    if free < needed:
        raise HTTPException(
            status_code=507,
            detail=f"{what} needs {needed // (1024 * 1024)} MB "
            f"free beside the knowledge bases ({root}); {free // (1024 * 1024)} MB "
            "is.",
        )


async def check_importable(manifest: KnowledgeBaseManifest) -> None:
    """Refuse (409) a version whose index this VISTA cannot search."""
    reason = incompatibility(manifest, await indexer.store_format())
    if reason is not None:
        raise HTTPException(status_code=409, detail=reason)


def _remove_stores(kb_dir: Path, keep: str, old: list[Path]) -> None:
    """
    Remove what an import replaced: the old files and store (`old`), every
    `store-*` folder but `keep`, and the canonical `pdfs/` and `rag_db/`,
    which `CURRENT` now overrides. Only inside `kb_dir`, and best effort: one
    the MCP server still has open (on Windows) stays until a later import or
    the knowledge base's deletion. Called under the slug's lock, so no other
    import's new folder is among them.
    """
    targets = [p for p in old if p.resolve().is_relative_to(kb_dir.resolve())]
    targets += [p for p in kb_dir.glob("store-*") if p.is_dir() and p.name != keep]
    targets += [kb_dir / "pdfs", kb_dir / "rag_db"]
    kept = (kb_dir / keep).resolve()
    for target in targets:
        # Never the new store, nor a folder holding it (a legacy store that
        # was `kb_dir` itself).
        if kept.is_relative_to(target.resolve()):
            continue
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)


async def import_knowledge_base(
    session: AsyncSession,
    local: LocalSettingsPublic,
    item_id: uuid.UUID,
    *,
    version: int | None = None,
    rename_to: str | None = None,
    replace_modified: bool = False,
    planned: RemoteVersion | None = None,
) -> KnowledgeBaseImportResult:
    """
    Import a version (by default the head) of a knowledge base on the server
    in effect: its files and index, ready to search.

    The copy here from the same item is updated in place, keeping its slug;
    with local changes, that needs `replace_modified`, else
    `KnowledgeBaseConflict` (`modified`), as does a change made while
    downloading. Otherwise a new knowledge base is made, with an id of its own,
    under the item's slug or `rename_to`; one in use is `name_taken`. A version
    whose index this VISTA cannot search is refused before it is downloaded.

    `planned` is that version, fetched already by a caller that checked it (a
    project import), so it is not fetched again.
    """
    url = required_url(local)
    async with client_for(local) as client:
        if planned is not None:
            remote, manifest = planned, manifest_of(planned)
            number = planned.version
            # An item's name is its publisher's slug, which the manifest records.
            item_name = manifest.slug if manifest is not None else str(item_id)
        else:
            item = await client.get_item(item_id)
            if item.kind != KB_KIND:
                raise HTTPException(
                    status_code=400,
                    detail=f"{item.name} on the collaboration server is a "
                    f"{item.kind}, not a knowledge base.",
                )
            item_name = item.name
            number = version if version is not None else item.head_version
            remote = await client.get_version(item_id, number)
            manifest = manifest_of(remote)
        if manifest is None or remote.blob_ref is None:
            raise CollaborationError(
                f"Version {number} of {item_name} on the collaboration server is "
                "not a knowledge base this VISTA can import."
            )
        first = await linked_knowledge_base(session, url, item_id)
        if first is not None:
            slug = first.slug
        else:
            chosen = (rename_to or "").strip()
            slug = chosen or item_name
            _check_slug(slug, chosen=bool(chosen))
        async with kb_lock(slug):
            # What another request committed while this one waited.
            session.expire_all()
            existing = await linked_knowledge_base(session, url, item_id)
            if (existing is None) != (first is None) or (
                existing is not None and existing.slug != slug
            ):
                raise HTTPException(
                    status_code=409,
                    detail=f"{item_name} was imported or deleted here meanwhile; "
                    "import it again.",
                )
            return await _import(
                session,
                client,
                url=url,
                item_id=item_id,
                item_name=item_name,
                number=number,
                blob_ref=remote.blob_ref,
                manifest=manifest,
                slug=slug,
                existing=existing,
                replace_modified=replace_modified,
            )


async def _import(
    session: AsyncSession,
    client: CollaborationClient,
    *,
    url: str,
    item_id: uuid.UUID,
    item_name: str,
    number: int,
    blob_ref: str,
    manifest: KnowledgeBaseManifest,
    slug: str,
    existing: KnowledgeBaseTable | None,
    replace_modified: bool,
) -> KnowledgeBaseImportResult:
    """`import_knowledge_base` under the slug's lock."""
    if existing is not None:
        if existing.remote_version == number and not existing.modified:
            return KnowledgeBaseImportResult(
                status="unchanged",
                knowledge_base=KnowledgeBasePublic.model_validate(existing),
            )
        if existing.modified and not replace_modified:
            raise _changed_here(existing, number)
        require_idle(existing, "import it")
        before = kb_content(existing)
        old = [Path(existing.pdfs_dir), Path(existing.rag_db_path)]
    else:
        if await get_kb_optional(session, slug) is not None:
            raise _name_taken(slug)
        before, old = None, []

    await check_importable(manifest)
    check_space(manifest.archive_size, f"Importing {manifest.name}")
    kb_dir = kb_folder(slug)
    async with _incoming() as tmp:
        archive = tmp / ARCHIVE_NAME
        await client.download_blob(
            item_id,
            number,
            archive,
            blob_ref=blob_ref,
            max_bytes=manifest.archive_size,
        )
        unpacked = tmp / "unpacked"
        try:
            await asyncio.to_thread(
                unpack_knowledge_base, archive, unpacked, has_index=manifest.has_index
            )
        except (ArchiveError, OSError) as e:
            raise HTTPException(
                status_code=400, detail=f"Cannot import {item_name}: {e}"
            ) from e
        kb_dir.mkdir(parents=True, exist_ok=True)
        store_name = f"store-{uuid.uuid4().hex[:12]}"
        dest = kb_dir / store_name
        os.replace(unpacked, dest)

    previous = read_current(kb_dir)
    try:
        now = now_iso()
        if existing is None:
            # Asked again: a knowledge base created (not imported) here while
            # downloading takes no lock.
            if await get_kb_optional(session, slug) is not None:
                raise _name_taken(slug)
            # An id of its own, never one from elsewhere: the seeded knowledge
            # bases share fixed ids across installations.
            row = KnowledgeBaseTable(
                slug=slug,
                name=manifest.name,
                pdfs_dir="",
                rag_db_path="",
                publications=[],
                build_status="pending",
                created_at=now,
                updated_at=now,
                origin_server=url,
                remote_id=item_id,
            )
            status: ImportStatus = "imported"
        else:
            # A file added or deleted, or indexing begun, while downloading.
            await reread(session, existing)
            if kb_content(existing) != before or indexer.get_progress(
                existing.rag_db_path
            ):
                raise _changed_here(existing, number)
            row = existing
            status = "updated"
        row.name = manifest.name
        row.description = manifest.description
        row.pdfs_dir = str(dest / "pdfs")
        row.rag_db_path = str(dest / "rag_db")
        row.publications = cast(
            list[Publication],
            [p.model_dump(mode="json") for p in _shipped(manifest.publications)],
        )
        row.build_status = manifest.build_status
        row.last_built_at = manifest.last_built_at
        row.shared_with_mcp = False
        row.updated_at = now
        row.remote_version = number
        row.modified = False
        write_current(kb_dir, store_name)
        session.add(row)
        await session.commit()
    except BaseException:
        await session.rollback()
        try:
            write_current(kb_dir, previous)
        finally:
            await asyncio.to_thread(shutil.rmtree, dest, ignore_errors=True)
        raise
    await session.refresh(row)

    for path in old:
        indexer.invalidate_rag_cache(str(path))
        indexer.clear_progress(str(path))
    if existing is not None:
        # Only what this knowledge base had here: a new one leaves a folder's
        # data that no row describes alone.
        await asyncio.to_thread(_remove_stores, kb_dir, store_name, old)
    return KnowledgeBaseImportResult(
        status=status, knowledge_base=KnowledgeBasePublic.model_validate(row)
    )
