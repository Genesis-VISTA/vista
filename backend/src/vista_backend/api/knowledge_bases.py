"""
Knowledge Bases API router.

Mirrors the projects router shape (list / get / create / update /
delete) plus per-KB publication endpoints for uploading PDFs and
downloading them back.

Indexing happens asynchronously as a FastAPI background task — the
POST returns immediately with the new publications marked `queued`,
and the indexer (see `utils.indexer.index_publications`) flips them
to `indexing` and then `indexed` / `failed` as it works. The UI
polls GET on this KB and watches the per-publication `index_status`
field to render progress; an in-memory progress snapshot is also
attached via `IndexProgress` for finer-grained feedback during a
single paper's run.
"""
from __future__ import annotations

import logging
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..config import settings
from ..db.db import SessionDep, commit_with_retry, get_engine
from ..db.schemas import (
    IndexProgress,
    KnowledgeBaseCreate,
    KnowledgeBasePublic,
    KnowledgeBaseTable,
    KnowledgeBaseUpdate,
    Publication,
)
from ..services import knowledge_base as kb_service
from ..utils import indexer
from ..utils.misc import write_file_unique
from sqlmodel.ext.asyncio.session import AsyncSession


logger = logging.getLogger("vista.knowledge_bases")

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# PDF filename sanitization — collapse anything outside [A-Za-z0-9._-]
# to underscores, strip path components. Mirrors uploads.py's helper.
_PDF_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]")
MAX_PDF_SIZE_BYTES = 50 * 1024 * 1024  # 50 MiB per file


def _sanitize_pdf_filename(name: str | None) -> str:
    if not name or name in (".", ".."):
        name = "upload.pdf"
    base = Path(name).name
    cleaned = _PDF_FILENAME_RE.sub("_", base)
    if not cleaned.lower().endswith(".pdf"):
        cleaned = cleaned + ".pdf"
    return cleaned


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _publication_dict(p: Publication | dict) -> dict:
    """Coerce a Publication / dict into the JSON-column friendly shape."""
    if isinstance(p, Publication):
        return p.model_dump(mode="json")
    return dict(p)


def _reconcile_with_disk(
    pdfs_dir_str: str,
    publications: list,
) -> tuple[list[dict], bool]:
    """
    Walk `pdfs_dir_str` and reconcile against `publications` (a list of
    Publication objects or dicts). Returns `(new_publications_dicts,
    changed)`. The input list is not mutated; callers that want the new
    state get it via the return value.

      - Adds stub Publication entries for files on disk we don't track.
      - Flips `has_pdf=False` for tracked publications whose files are
        no longer on disk (kept around because chroma may still hold
        their chunks/citation).
    """
    pdfs_dir = Path(pdfs_dir_str)
    on_disk: dict[str, int] = {}
    if pdfs_dir.is_dir():
        for entry in pdfs_dir.iterdir():
            if not entry.is_file():
                continue
            if entry.suffix.lower() != ".pdf":
                continue
            try:
                on_disk[entry.name] = entry.stat().st_size
            except OSError:
                continue

    pubs = [
        Publication.model_validate(p) if isinstance(p, dict) else p.model_copy()
        for p in publications
    ]
    by_name = {p.filename: p for p in pubs}
    changed = False
    now = _now_iso()

    # New files on disk → stub publication.
    for name, size in on_disk.items():
        if name not in by_name:
            pubs.append(Publication(
                filename=name,
                size=size,
                added_at=now,
                has_pdf=True,
                index_status="unindexed",
            ))
            changed = True
            continue
        # Existing publication: update size + has_pdf if they drift.
        pub = by_name[name]
        if not pub.has_pdf or pub.size != size:
            pub.has_pdf = True
            pub.size = size
            changed = True

    # Tracked publications whose PDF disappeared from disk. Don't drop
    # them — chroma may still have the chunks/citation. Just flip
    # has_pdf so the UI can label them "indexed only".
    for pub in pubs:
        if pub.filename not in on_disk and pub.has_pdf:
            pub.has_pdf = False
            pub.size = 0
            changed = True

    return [_publication_dict(p) for p in pubs], changed


# Cache the chroma citation read so repeated GETs don't keep opening the
# sqlite file. Keyed by (rag_db_path, chroma.sqlite3 mtime) — when chroma
# is rewritten (e.g. indexing landed new rows) the mtime bumps and we
# re-read. Cache value is the list of citation metadata dicts.
_CHROMA_CITATIONS_CACHE: dict[tuple[str, float], list[dict]] = {}


def _read_chroma_citations(rag_db_path: str) -> list[dict] | None:
    """
    Pull every row from the `citations` collection at `rag_db_path` and
    return their metadata dicts. Returns None if the chroma DB doesn't
    exist or can't be opened — callers should treat that as "nothing
    to reconcile from chroma".

    Synchronous and chromadb-import-heavy. Called from a thread
    executor in `_reconcile_with_chroma`.

    NOTE on concurrency: This function opens its own `chromadb.PersistentClient`
    against `rag_db_path` for each call. If the indexer is *also* actively
    writing to that same path (we're called from the UI's 5s polling
    reconciler during a long indexing run), then opening a second client
    runs chromadb's migration validation against the same `chroma.sqlite3`
    file. Depending on filesystem behavior and chromadb version, this can
    cause the indexer's open SQLite handle to see `SQLITE_READONLY_DBMOVED`
    (code 1032) on its next write — chromadb's "PersistentClient is a
    singleton per path" rule, violated. We avoid that by bailing out
    cleanly while an indexer is in flight; the reconciler will pick up
    chroma's state on the next poll after indexing completes.
    """
    db_path = Path(rag_db_path)
    sqlite_file = db_path / "chroma.sqlite3"
    if not sqlite_file.is_file():
        return None

    # If the indexer is currently writing to this path, do NOT open a
    # second chromadb client against it. The indexer holds the canonical
    # client for the duration of the run; opening another one risks
    # SQLITE_READONLY_DBMOVED on the indexer's handle. Returning None
    # here means "no chroma update this cycle"; the GET handler treats
    # that as a no-op and the UI's next poll after indexing finishes
    # will reconcile normally.
    pending = indexer.get_progress(rag_db_path)
    if pending is not None:
        logger.debug(
            "Skipping chroma read for %s: indexer pending (phase=%s)",
            rag_db_path, pending.get("phase"),
        )
        return None

    try:
        mtime = sqlite_file.stat().st_mtime
    except OSError:
        return None
    cache_key = (str(db_path.resolve()), mtime)
    cached = _CHROMA_CITATIONS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # If we get here, we're about to open chromadb. Log this so a
    # SQLITE_READONLY_DBMOVED traceback later can be correlated with the
    # exact opens that caused it.
    logger.warning(
        "Opening chromadb client for citation reconcile at %s "
        "(indexer.get_progress returned None)",
        rag_db_path,
    )

    # Lazy import so a backend with no KB usage doesn't pay the
    # chromadb import cost on startup. First call here is slow
    # (~1s for the import); subsequent calls are cheap.
    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings
    except ImportError:
        logger.warning("chromadb not installed; cannot read citations from %s", rag_db_path)
        return None

    try:
        # Settings MUST match build_rag.TextRAG's client or ChromaDB will throw
        client = chromadb.PersistentClient(
            path=str(db_path),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to open chroma at %s: %s", rag_db_path, exc)
        return None

    try:
        collection = client.get_collection("citations")
    except Exception:
        # Collection doesn't exist — happens for fresh KBs that have
        # never been indexed. Cache an empty result so we don't keep
        # paying the open cost.
        _CHROMA_CITATIONS_CACHE[cache_key] = []
        return []

    try:
        result = collection.get(include=["metadatas"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to query chroma citations at %s: %s", rag_db_path, exc)
        return None

    metas = list(result.get("metadatas") or [])
    # Filter to only dict-shaped rows with a `source` filename — anything
    # else is malformed and would just break the merge.
    citations = [m for m in metas if isinstance(m, dict) and isinstance(m.get("source"), str)]
    _CHROMA_CITATIONS_CACHE[cache_key] = citations

    # Don't let the cache grow unbounded — keep just the most recent
    # entry per rag_db_path. The mtime in the key means stale entries
    # accumulate naturally otherwise.
    for stale_key in list(_CHROMA_CITATIONS_CACHE.keys()):
        if stale_key[0] == cache_key[0] and stale_key != cache_key:
            _CHROMA_CITATIONS_CACHE.pop(stale_key, None)

    return citations


def _merge_chroma_metadata_into_pub(pub: Publication, meta: dict) -> bool:
    """
    Fill in publication fields from a chroma citation metadata dict.
    Only fills empty fields — never overwrites. Returns True if
    anything changed.

    The chroma payload comes from `build_rag.py`'s `_citation_to_metadata`:
    scalar fields are strings, list fields (`authors`, `keywords`) are
    JSON-encoded strings. We decode the lists back into arrays before
    storing on the Publication.
    """
    import json as _json
    changed = False
    for field in ("title", "abstract", "journal", "volume", "issue",
                  "pages", "year", "doi", "publisher"):
        if not getattr(pub, field, None):
            value = meta.get(field)
            if isinstance(value, str) and value.strip():
                setattr(pub, field, value)
                changed = True
    for field in ("authors", "keywords"):
        if not getattr(pub, field, None):
            value = meta.get(field)
            if isinstance(value, str):
                try:
                    parsed = _json.loads(value)
                    if isinstance(parsed, list) and parsed:
                        setattr(pub, field, parsed)
                        changed = True
                except (ValueError, TypeError):
                    pass
            elif isinstance(value, list) and value:
                setattr(pub, field, list(value))
                changed = True
    return changed


def _reconcile_with_chroma(
    rag_db_path: str,
    publications: list,
) -> tuple[list[dict], bool]:
    """
    Read citation metadata from the chroma DB at `rag_db_path` and
    reconcile against `publications`. Returns `(new_publications_dicts,
    changed)`; the input is not mutated.

      - Fills in citation fields on tracked publications that don't have
        them yet (common after a fresh-from-LFS checkout where chroma is
        present but the publications list is empty).
      - Adds `has_pdf=False`, `index_status="indexed"` stub publications
        for citation rows whose filename isn't tracked at all (the
        "indexed only" case — chroma has chunks for it but the source
        PDF isn't in pdfs_dir).

    Safe to call when chroma doesn't exist — returns `(publications, False)`
    quietly.
    """
    citations = _read_chroma_citations(rag_db_path)
    if not citations:
        return [_publication_dict(p) for p in publications], False

    pubs = [
        Publication.model_validate(p) if isinstance(p, dict) else p.model_copy()
        for p in publications
    ]
    by_name = {p.filename: p for p in pubs}
    changed = False
    now = _now_iso()

    for meta in citations:
        filename = meta.get("source")
        if not isinstance(filename, str) or not filename:
            continue
        existing = by_name.get(filename)
        if existing is None:
            # Synthesize a stub. has_pdf is False unless the disk
            # reconciler later flips it (it runs first, then us, so by
            # the time we get here we know the PDF wasn't on disk).
            new_pub = Publication(
                filename=filename,
                size=0,
                added_at=now,
                has_pdf=False,
                index_status="indexed",
                indexed_at=None,
            )
            _merge_chroma_metadata_into_pub(new_pub, meta)
            pubs.append(new_pub)
            by_name[filename] = new_pub
            changed = True
        else:
            # Fill in missing fields on a tracked publication. Also
            # promote index_status to "indexed" if it's still showing
            # "unindexed" — a citation row in chroma means we ran
            # against this PDF at some point.
            if _merge_chroma_metadata_into_pub(existing, meta):
                changed = True
            if existing.index_status == "unindexed":
                existing.index_status = "indexed"
                changed = True

    return [_publication_dict(p) for p in pubs], changed


async def _reconcile_with_chroma_async(
    rag_db_path: str,
    publications: list,
) -> tuple[list[dict], bool]:
    """
    Async wrapper for `_reconcile_with_chroma`. The chroma read does
    sqlite IO and (on first call) imports chromadb — both block the
    event loop noticeably. Run in the indexer's thread executor so
    a slow chroma open doesn't stall the rest of the API.
    """
    import asyncio
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        indexer._executor, _reconcile_with_chroma, rag_db_path, publications,
    )


def _public_with_progress(
    kb: KnowledgeBaseTable,
    *,
    publications_override: list[dict] | None = None,
    updated_at_override: str | None = None,
) -> dict[str, Any]:
    """
    Project a table row into the public shape + attach live progress.

    Optional overrides let GET endpoints return a reconciled view without
    actually mutating the session-attached row — that's important because
    mutating the row would mark the request's session dirty and trigger
    a write at scope-exit commit, which is exactly the contention point
    we're trying to avoid. Pass the reconciler's output here instead.
    """
    public = KnowledgeBasePublic.model_validate(kb).model_dump(mode="json")
    if publications_override is not None:
        public["publications"] = publications_override
    if updated_at_override is not None:
        public["updated_at"] = updated_at_override
    snap = indexer.get_progress(kb.rag_db_path)
    public["index_progress"] = snap
    return public


# ---------------------------------------------------------------------------
# Lock-contention strategy
#
# SQLite is single-writer. With WAL + busy_timeout=30s (see db.py), readers
# never block writers and one waiting writer will retry-wait. But two
# *concurrent* writers — the indexer background task plus a UI poll's
# reconciler, or a POST plus a reconciler — can still pile up enough that
# the busy_timeout expires.
#
# Two-pronged strategy:
#   1. GET handlers NEVER call session.flush() themselves. If the reconciler
#      decides something drifted, the GET hands off persistence to a
#      background task on a fresh, short-lived session (`_schedule_persist`).
#      The GET response uses the in-memory reconciled view immediately,
#      and the session-attached row is left untouched so the request-scope
#      session has nothing to commit at scope exit.
#   2. All commits (request-scope and background) go through
#      `commit_with_retry` from db.py, which retries on "database is locked"
#      with exponential backoff. User-initiated writes get a 5xx if every
#      retry fails; reconciler writes silently drop and try again next poll.
#   3. Reconciler persists are *coalesced* per KB. Multiple GETs landing in
#      quick succession (polling, list+detail, etc) would otherwise queue
#      multiple background tasks for the same KB, each opening its own
#      AsyncSession and burning a connection from the pool while it retries.
#      Instead, we maintain one "latest pending payload" per KB id; a single
#      worker per KB drains it. Concurrent schedule calls just overwrite
#      the pending payload — no duplicate sessions, no pool exhaustion.
# ---------------------------------------------------------------------------


# Per-KB coalesced persist state. Maps kb_id → (publications, updated_at).
# `_persist_pending` is the latest reconciled snapshot waiting to be
# written. `_persist_locks` ensures one worker per KB.
_persist_pending: dict[uuid.UUID, tuple[list[dict], str]] = {}
_persist_locks: dict[uuid.UUID, "asyncio.Lock"] = {}


def _persist_lock_for(kb_id: uuid.UUID) -> "asyncio.Lock":
    import asyncio
    lock = _persist_locks.get(kb_id)
    if lock is None:
        lock = asyncio.Lock()
        _persist_locks[kb_id] = lock
    return lock


async def _drain_pending_persist(kb_id: uuid.UUID) -> None:
    """
    Background worker: drain whatever is in `_persist_pending[kb_id]`. If
    a second `_schedule_persist` came in while this worker was sleeping/
    committing, it updated the pending entry and we'll see the latest
    value when we re-check inside the lock. We loop until the pending
    entry is gone, then release.

    Best-effort — if SQLite stays locked through every retry, drop the
    write; the reconciler is idempotent and the next poll will queue it
    again.
    """
    lock = _persist_lock_for(kb_id)
    async with lock:
        engine = get_engine()
        while True:
            entry = _persist_pending.pop(kb_id, None)
            if entry is None:
                return
            publications, updated_at = entry
            async with AsyncSession(engine) as session:
                try:
                    kb = await session.get(KnowledgeBaseTable, kb_id)
                    if kb is None:
                        # KB was deleted between the GET that scheduled
                        # this task and now. Nothing to persist.
                        continue
                    kb.publications = publications
                    kb.updated_at = updated_at
                    session.add(kb)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Reconciler persist setup failed for kb_id=%s",
                        kb_id,
                    )
                    await session.rollback()
                    continue
                await commit_with_retry(
                    session, context=f"reconciler_persist:{kb_id}",
                )


def _schedule_persist(
    background_tasks: BackgroundTasks,
    kb: KnowledgeBaseTable,
    publications: list[dict],
) -> str:
    """
    Snapshot the reconciled publications and queue a background task to
    write them back on a fresh session. Returns the `updated_at` ISO
    string that the caller should attach to the response dict.

    Coalesced per KB: if a previous schedule call's task hasn't run yet,
    we overwrite its payload and don't spawn a second task. The worker
    loop drains the latest value when it gets the per-KB lock. This
    prevents pool exhaustion when fast polling produces many drift
    detections in quick succession.

    We deliberately do NOT mutate the session-attached `kb` object — that
    would mark the request's session dirty and trigger the very write
    contention we're trying to avoid.
    """
    now = _now_iso()
    had_pending = kb.id in _persist_pending
    _persist_pending[kb.id] = (publications, now)
    if not had_pending:
        background_tasks.add_task(_drain_pending_persist, kb.id)
    return now


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.get("")
async def list_knowledge_bases(
    session: SessionDep,
    background_tasks: BackgroundTasks,
) -> list[dict[str, Any]]:
    rows = await kb_service.list_kbs(session)
    out: list[dict[str, Any]] = []
    for row in rows:
        # Reconcile disk first (cheap walk of pdfs_dir), then chroma
        # (synthesizes indexed-only entries from chroma's `citations`
        # collection when present). The chroma read is cached by
        # (path, sqlite mtime) so successive GETs are fast.
        #
        # The reconcilers are pure: they take the current publications
        # list and return a new one. We do NOT mutate `row` here, since
        # that would mark the request's session dirty and trigger a write
        # at scope-exit commit — exactly the lock contention we want to
        # avoid. If anything changed, a background task on a fresh session
        # persists the result with retry.
        pubs_after_disk, changed_disk = _reconcile_with_disk(
            row.pdfs_dir, row.publications,
        )
        pubs_after_chroma, changed_chroma = await _reconcile_with_chroma_async(
            row.rag_db_path, pubs_after_disk,
        )
        if changed_disk or changed_chroma:
            new_updated_at = _schedule_persist(
                background_tasks, row, pubs_after_chroma,
            )
            out.append(_public_with_progress(
                row,
                publications_override=pubs_after_chroma,
                updated_at_override=new_updated_at,
            ))
        else:
            out.append(_public_with_progress(row))
    out.sort(key=lambda kb: kb["slug"])
    return out


@router.get("/{slug}")
async def get_knowledge_base(
    slug: str,
    session: SessionDep,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    kb = await kb_service.get_kb(session, slug)
    pubs_after_disk, changed_disk = _reconcile_with_disk(
        kb.pdfs_dir, kb.publications,
    )
    pubs_after_chroma, changed_chroma = await _reconcile_with_chroma_async(
        kb.rag_db_path, pubs_after_disk,
    )
    if changed_disk or changed_chroma:
        new_updated_at = _schedule_persist(
            background_tasks, kb, pubs_after_chroma,
        )
        return _public_with_progress(
            kb,
            publications_override=pubs_after_chroma,
            updated_at_override=new_updated_at,
        )
    return _public_with_progress(kb)


@router.post("", status_code=201)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate, session: SessionDep,
) -> dict[str, Any]:
    row = await kb_service.create_kb(session, payload)
    return _public_with_progress(row)


@router.put("/{slug}")
async def update_knowledge_base(
    slug: str, updates: KnowledgeBaseUpdate, session: SessionDep,
) -> dict[str, Any]:
    kb = await kb_service.update_kb(session, slug, updates)
    return _public_with_progress(kb)


@router.delete("/{slug}", status_code=204)
async def delete_knowledge_base(slug: str, session: SessionDep) -> None:
    """
    Delete a knowledge base end-to-end:
      - drop the SQL row (vista.db)
      - rmtree the KB's PDFs directory
      - rmtree the KB's ChromaDB directory
      - clear any in-memory indexer progress for this KB

    Built-in KBs cannot be deleted. For everything else we apply a
    safety check: the on-disk paths must live underneath the configured
    `knowledge_bases_dir`. A misconfigured DB row pointing at, say,
    `/` or `~` would otherwise be a catastrophic footgun.

    The DB row is dropped first so concurrent reads stop seeing the KB
    immediately; filesystem cleanup happens after. If the filesystem
    step partially fails, the KB no longer appears in the UI and the
    orphaned directories can be cleaned up manually — that's a strictly
    better failure mode than leaving the DB out of sync with disk.
    """
    pdfs_dir_str, rag_db_path_str = await kb_service.delete_kb(session, slug)
    pdfs_dir = Path(pdfs_dir_str)
    rag_db_path = Path(rag_db_path_str)

    # Filesystem cleanup. We do this after the DB row is gone, but
    # inside the same request so the user sees "deleted" only once
    # everything is actually gone (or we've at least tried).
    kb_root = settings.knowledge_bases_dir.resolve()

    def _is_safe(target: Path) -> bool:
        """True iff `target` lives under `knowledge_bases_dir`."""
        try:
            target.relative_to(kb_root)
            return True
        except ValueError:
            return False

    for target in (pdfs_dir, rag_db_path):
        if not target.exists():
            continue
        if not _is_safe(target):
            logger.warning(
                "Refusing to delete KB path outside knowledge_bases_dir: "
                "%s (root=%s)", target, kb_root,
            )
            continue
        try:
            shutil.rmtree(target)
        except OSError:
            # Log and continue — the SQL row is already gone, and a
            # failure here usually means a file lock (e.g. chromadb
            # mid-write). The user can re-run delete or clean up
            # by hand.
            logger.exception(
                "Failed to remove %s while deleting KB %s", target, slug,
            )

    # Best effort: if both pdfs/ and rag_db/ lived under a parent
    # directory dedicated to this KB (the layout `_schedule_persist`'s
    # creator uses: `data/knowledge-bases/<slug>/{pdfs,rag_db}`), the
    # parent is now empty. Remove it too so we don't leave litter.
    parents_to_check = {pdfs_dir.parent, rag_db_path.parent}
    for parent in parents_to_check:
        if parent == kb_root:
            continue  # never rmdir the root itself
        if not _is_safe(parent):
            continue
        try:
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Publications
# ---------------------------------------------------------------------------

@router.post("/{slug}/publications", status_code=201)
async def add_publications(
    slug: str,
    files: list[UploadFile],
    background_tasks: BackgroundTasks,
    session: SessionDep,
) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="No files were provided.")

    kb = await kb_service.get_kb(session, slug)
    pdfs_dir = Path(kb.pdfs_dir)
    pdfs_dir.mkdir(parents=True, exist_ok=True)

    pubs = [
        Publication.model_validate(p) if isinstance(p, dict) else p
        for p in kb.publications
    ]
    existing_names = {p.filename for p in pubs}
    now = _now_iso()
    added_names: list[str] = []

    for file in files:
        contents = await file.read()
        if len(contents) > MAX_PDF_SIZE_BYTES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"File '{file.filename}' exceeds the "
                    f"{MAX_PDF_SIZE_BYTES // (1024 * 1024)} MiB upload limit."
                ),
            )
        # Sanitize, then de-dupe within this KB by appending -1, -2, ...
        safe = _sanitize_pdf_filename(file.filename)
        # Re-use the unique-write helper from uploads.py.
        target = pdfs_dir / safe
        saved = write_file_unique(target, contents)
        # write_file_unique only avoids on-disk collisions; also check
        # the publications list in case of a phantom row (PDF deleted
        # but record remained).
        final_name = saved.name
        while final_name in existing_names:
            saved.unlink(missing_ok=True)
            saved = write_file_unique(pdfs_dir / safe, contents)
            final_name = saved.name
        existing_names.add(final_name)
        pubs.append(Publication(
            filename=final_name,
            size=len(contents),
            added_at=now,
            has_pdf=True,
            index_status="queued",
            index_error=None,
        ))
        added_names.append(final_name)

    # Adding new PDFs invalidates any prior "ready" build state.
    new_build_status = "stale" if kb.build_status == "ready" else kb.build_status

    kb.publications = [_publication_dict(p) for p in pubs]
    kb.build_status = new_build_status
    kb.updated_at = now
    session.add(kb)
    await session.flush()
    await session.refresh(kb)

    # Spawn the indexer in the background. We commit the current
    # session first (so the queued statuses are persisted) — the
    # background task will open its own session to update results.
    rag_db_path = kb.rag_db_path
    pdfs_dir_str = str(pdfs_dir)
    kb_id = kb.id
    # CRITICAL: register the pending run synchronously, BEFORE FastAPI
    # gets a chance to start dispatching polls' reconcilers. The
    # reconciler skips opening chromadb when an indexer run is in flight
    # for the same path (see `_read_chroma_citations`); if we deferred
    # this to the background task itself, the gap between scheduling
    # and execution is wide enough for the first poll to slip in and
    # open chromadb against the path, causing SQLITE_READONLY_DBMOVED
    # on the indexer's first write.
    indexer.register_pending(rag_db_path, total=len(added_names))
    background_tasks.add_task(
        _run_indexer_and_persist,
        kb_id=kb_id,
        rag_db_path=rag_db_path,
        pdfs_dir=pdfs_dir_str,
        filenames=list(added_names),
    )

    return _public_with_progress(kb)


@router.get("/{slug}/publications/{filename}")
async def download_publication(
    slug: str, filename: str, session: SessionDep,
) -> FileResponse:
    kb = await kb_service.get_kb(session, slug)
    # Reject anything with path-separator chars; we look up by basename only.
    if filename != Path(filename).name or filename in ("", ".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename.")
    pdf_path = Path(kb.pdfs_dir) / filename
    if not pdf_path.is_file():
        raise HTTPException(status_code=404, detail="Publication not found.")
    return FileResponse(
        pdf_path,
        filename=filename,
        media_type="application/pdf",
    )


@router.delete("/{slug}/publications/{filename}", status_code=204)
async def delete_publication(
    slug: str, filename: str, session: SessionDep,
) -> None:
    kb = await kb_service.get_kb(session, slug)
    if filename != Path(filename).name or filename in ("", ".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename.")
    pdf_path = Path(kb.pdfs_dir) / filename
    if pdf_path.is_file():
        pdf_path.unlink()

    pubs = [
        Publication.model_validate(p) if isinstance(p, dict) else p
        for p in kb.publications
    ]
    new_pubs = [p for p in pubs if p.filename != filename]
    if len(new_pubs) == len(pubs):
        raise HTTPException(status_code=404, detail="Publication not found.")
    kb.publications = [_publication_dict(p) for p in new_pubs]
    kb.updated_at = _now_iso()
    session.add(kb)
    # NB: this leaves the chroma rows behind. Deleting from chroma
    # would require opening the collection and calling .delete(...)
    # with the row IDs. Acceptable trade-off for now; the rag_search
    # tool will still return them but the UI hides them via has_pdf.


# ---------------------------------------------------------------------------
# Background task: run indexer, persist results
# ---------------------------------------------------------------------------

async def _run_indexer_and_persist(
    *,
    kb_id: uuid.UUID,
    rag_db_path: str,
    pdfs_dir: str,
    filenames: list[str],
) -> None:
    """
    Background task: invoke the indexer for the given filenames, then
    open a fresh DB session and merge results into the KB row.

    Runs entirely outside the request lifecycle, so we can't reuse the
    request's session. To minimize the window during which we hold a
    write transaction against vista.db (which would block concurrent
    GET requests that also need to flush reconciler changes), we
    pre-compute the new publications list outside the session, then
    open the session only for the final UPDATE + commit.
    """
    try:
        results = await indexer.index_publications(
            rag_db_path=rag_db_path,
            pdfs_dir=pdfs_dir,
            filenames=filenames,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Indexer crashed for kb_id=%s", kb_id)
        # Clear the pending-progress flag we set at upload time, since
        # index_publications didn't reach its own cleanup. Otherwise
        # the reconciler would refuse to open chromadb for this path
        # for the next 30 minutes.
        indexer._clear_progress(rag_db_path)
        results = [
            {
                "filename": f,
                "status": "failed",
                "error": "Indexer crashed; see backend logs.",
                "chunk_count": 0,
                "citation": None,
                "citation_status": "failed",
            }
            for f in filenames
        ]

    engine = get_engine()

    # First read: pull the current publications list, then close the txn
    # immediately so any concurrent GET reconciler can proceed.
    async with AsyncSession(engine) as session:
        current = await session.get(KnowledgeBaseTable, kb_id)
        if current is None:
            logger.warning("kb_id=%s vanished while indexer was running", kb_id)
            return
        # Snapshot the JSON column. After this we're done with the row
        # for now and the txn closes when the `with` exits.
        publications_snapshot = list(current.publications or [])

    # Merge results into the snapshot in memory — no DB locks held.
    pubs = [
        Publication.model_validate(p) if isinstance(p, dict) else p
        for p in publications_snapshot
    ]
    by_name = {p.filename: p for p in pubs}
    now = _now_iso()
    any_indexed = False

    for res in results:
        fname = res.get("filename")
        if not fname or fname not in by_name:
            continue
        pub = by_name[fname]
        status = res.get("status")
        if status in ("indexed", "skipped"):
            pub.index_status = "indexed"
            pub.index_error = None
            pub.indexed_at = now if status == "indexed" else (pub.indexed_at or now)
            any_indexed = True
            citation = res.get("citation")
            if isinstance(citation, dict):
                _merge_citation_into_pub(pub, citation)
        else:
            pub.index_status = "failed"
            pub.index_error = res.get("error") or "Indexing failed."

        # Citation outcome is tracked independently of chunk-indexing
        # outcome. A PDF can be fully indexed (chunks in chroma) while
        # citation extraction was disabled, failed, or returned no
        # parseable result — surfacing that in the UI prevents users
        # from waiting forever on metadata that's never coming.
        cit_status = res.get("citation_status")
        if cit_status in ("extracted", "skipped", "failed", "disabled", "pending"):
            pub.citation_status = cit_status
        cit_err = res.get("citation_error")
        if isinstance(cit_err, str) and cit_err:
            pub.citation_error = cit_err
        elif cit_status in ("extracted", "skipped", "disabled"):
            # Clear any stale error from a previous failed attempt.
            pub.citation_error = None

    merged_publications = [_publication_dict(p) for p in pubs]

    # Second open: apply the merged result with a single tight UPDATE.
    # No reads here that might hold a SHARED lock and conflict with a
    # concurrent GET — go straight to write and commit. Combined with
    # WAL + busy_timeout=30s in db.py, any brief overlap with a GET's
    # session.flush() resolves by waiting rather than failing.
    async with AsyncSession(engine) as session:
        try:
            kb = await session.get(KnowledgeBaseTable, kb_id)
            if kb is None:
                logger.warning("kb_id=%s vanished mid-merge", kb_id)
                return
            kb.publications = merged_publications
            if any_indexed:
                kb.build_status = "ready"
                kb.last_built_at = now
            kb.updated_at = now
            session.add(kb)
        except Exception:  # noqa: BLE001
            await session.rollback()
            logger.exception("Failed to persist indexer results for kb_id=%s", kb_id)
            return
        # Best-effort commit. If we hit the retry budget the indexer
        # work itself isn't lost (chroma already has the chunks), but
        # the SQL row stays in "indexing" state until the next poll's
        # reconciler picks up the chroma citations.
        ok = await commit_with_retry(
            session, context=f"indexer_persist:{kb_id}",
        )
        if not ok:
            logger.warning(
                "Indexer persist for kb_id=%s gave up after retries; "
                "chroma is up to date but vista.db will catch up on next "
                "reconciler pass.", kb_id,
            )


def _merge_citation_into_pub(pub: Publication, citation: dict) -> None:
    """
    Fill in publication fields from a citation metadata dict, but only
    where the publication currently has no value. The indexer hands us
    ChromaDB-shaped metadata: list fields like `authors` come back as
    JSON-encoded strings (per build_rag.py's `_citation_to_metadata`),
    so we decode them first.
    """
    import json as _json

    for field in (
        "title", "abstract", "journal", "volume", "issue",
        "pages", "year", "doi", "publisher",
    ):
        if not getattr(pub, field, None):
            value = citation.get(field)
            if isinstance(value, str) and value.strip():
                setattr(pub, field, value)

    for field in ("authors", "keywords"):
        if not getattr(pub, field, None):
            value = citation.get(field)
            if isinstance(value, str):
                try:
                    parsed = _json.loads(value)
                    if isinstance(parsed, list):
                        setattr(pub, field, parsed)
                except (ValueError, TypeError):
                    pass
            elif isinstance(value, list):
                setattr(pub, field, list(value))
