"""
Knowledge Base indexer: thin async wrapper around build_rag.TextRAG.

`build_rag.py` lives at the repo root and owns the chunking, embedding,
and citation extraction. This module:

  - imports `TextRAG` lazily, so the expensive sentence-transformers
    / chromadb import doesn't run on backend startup (only when the
    first indexing call lands);
  - runs each per-PDF call in a thread executor so the FastAPI event
    loop stays responsive;
  - serializes concurrent indexing on the same rag_db (ChromaDB's
    SQLite backend is single-writer) using an asyncio lock per path;
  - publishes progress to an in-memory dict keyed by rag_db path so
    the API layer can render a progress bar.

Indexing semantics mirror the old subprocess design:
  - text chunks always indexed when a PDF is present
  - citation extraction is best-effort; runs first (so a hung LLM
    fails fast before we've polluted chroma with orphan chunks)
  - if Azure-style or OpenAI-compatible LLM credentials are absent,
    citation extraction is skipped silently (`citation_status` =
    `"disabled"` in the result)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("vista.indexer")

# Marker so users can confirm the patched indexer.py is what's actually
# loaded. Bump the date whenever this file changes in a way that affects
# user-visible behavior. Search the startup log for this string to verify.
_INDEXER_VERSION = "2026-05-18-vista-backend-model-fallback"
logger.warning("indexer.py loaded (version: %s)", _INDEXER_VERSION)


# ---------------------------------------------------------------------------
# Lazy import of build_rag.TextRAG
# ---------------------------------------------------------------------------
_TextRAG = None  # populated on first call


def _get_text_rag_cls():
    """
    Lazy-import TextRAG. Walks up from this file to find the repo root
    (where `build_rag.py` lives) and inserts it into sys.path. This
    avoids the user needing to install build_rag as a package — the
    repo layout has it at the root next to `backend/` and `mcp_servers/`.
    """
    global _TextRAG
    if _TextRAG is not None:
        return _TextRAG

    import sys

    here = Path(__file__).resolve()
    # utils -> vista_backend -> src -> backend -> repo root
    repo_root = here.parents[4]
    if not (repo_root / "build_rag.py").is_file():
        raise RuntimeError(
            f"Could not locate build_rag.py at {repo_root}. The KB indexer "
            f"expects the repo root to contain `build_rag.py`."
        )
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from build_rag import TextRAG  # type: ignore[import-not-found]

    _TextRAG = TextRAG
    return _TextRAG


# ---------------------------------------------------------------------------
# Per-rag-db serialization
#
# ChromaDB's persistent client backs onto SQLite, which is single-writer.
# Two indexing runs against the same path will conflict (one will block
# or fail). We serialize via an asyncio lock keyed by absolute rag_db
# path so a second upload to the same KB while the first is mid-index
# queues up rather than racing.
# ---------------------------------------------------------------------------
_db_locks: dict[str, asyncio.Lock] = {}


def _db_lock(rag_db_path: str) -> asyncio.Lock:
    key = str(Path(rag_db_path).resolve())
    if key not in _db_locks:
        _db_locks[key] = asyncio.Lock()
    return _db_locks[key]


# ---------------------------------------------------------------------------
# Per-path TextRAG instance cache.
#
# Why this exists: chromadb's PersistentClient is a process-wide singleton
# per path. Creating a second PersistentClient against the same path in
# the same process AFTER the first has been garbage-collected can fail
# with SQLITE_READONLY_DBMOVED (code 1032) on the second client's writes:
# WAL checkpointing rotates the sqlite file's inode while residual state
# from the GC'd client lingers, and the new client's handle sees the
# moved file.
#
# Symptom: first indexing run against a KB succeeds; a second run against
# the same KB (after the first's TextRAG was GC'd) fails on the chroma
# write step with "(code: 1032) attempt to write a readonly database".
#
# Fix: keep the TextRAG instance alive between runs by caching it per
# resolved rag_db_path. We re-stamp its mutable fields (force_reindex,
# extract_citations, pdf_folder) on each reuse — the chromadb client
# inside it stays the same.
#
# Memory: bounded by the number of KBs touched per backend session;
# each TextRAG holds a SentenceTransformer (~600MB). Process restart
# clears the cache.
_text_rag_instances: dict[str, Any] = {}


def invalidate_rag_cache(rag_db_path: str) -> None:
    """
    Drop any cached TextRAG instance for this path. Call this when a KB
    is being deleted, before its on-disk chroma directory is rmtree'd —
    otherwise a subsequent re-creation of the same slug would find a
    stale cached client pointing at the now-deleted directory and
    SQLITE_READONLY_DBMOVED would fire on its first write.

    The cached TextRAG instance itself just gets dereferenced — Python's
    GC will release it (and the underlying chromadb client) when no
    other reference remains. We don't try to "close" chromadb because
    chromadb has no close() method (see
    https://github.com/chroma-core/chroma/issues/5868).
    """
    key = str(Path(rag_db_path).resolve())
    if _text_rag_instances.pop(key, None) is not None:
        logger.info("Invalidated cached TextRAG for %s", key)


# ---------------------------------------------------------------------------
# Progress state
#
# Indexing runs are long; the UI polls. Rather than persisting progress
# to disk, we keep an in-memory dict keyed by rag_db path. The API
# layer reads this and embeds it in KB read responses.
# ---------------------------------------------------------------------------
_progress: dict[str, dict[str, Any]] = {}


def get_progress(rag_db_path: str) -> Optional[dict[str, Any]]:
    """Return the current progress snapshot for a rag_db, or None."""
    key = str(Path(rag_db_path).resolve())
    snap = _progress.get(key)
    if snap is None:
        return None
    # Drop stale progress: if started_at is more than 30 minutes ago
    # and we never saw a completion, treat as gone.
    started = snap.get("started_at", 0.0)
    if time.time() - started > 30 * 60:
        _progress.pop(key, None)
        return None
    return dict(snap)


def register_pending(rag_db_path: str, *, total: int) -> None:
    """
    Mark `rag_db_path` as having an indexer run pending. Called from the
    upload endpoint BEFORE FastAPI dispatches the background task that
    will run `index_publications`, to close a small race window:

      - Upload endpoint queues background task with `add_task`, returns.
      - FastAPI sends the response. UI starts polling.
      - The first poll's reconciler runs, calls `_read_chroma_citations`,
        which checks `get_progress(path)` to decide whether to open a
        chromadb client.
      - If the background task hasn't started yet (millisecond gap, or
        slower if the event loop is busy), `get_progress` returns None
        and the reconciler opens chromadb against the same path the
        indexer is about to open. chromadb's PersistentClient-singleton
        rule is violated, and the indexer's first write fails with
        SQLITE_READONLY_DBMOVED (code 1032).

    By stamping the progress dict synchronously here, the very next
    `get_progress` call sees a non-None entry and the reconciler stands
    down. The indexer overwrites this entry with real `loading_model`
    progress as soon as it actually starts.

    If the indexer somehow never runs (rare — background-task scheduling
    failure), the entry expires after 30 minutes via the stale-progress
    sweep in `get_progress`.
    """
    key = str(Path(rag_db_path).resolve())
    _progress[key] = {
        "phase": "loading_model",
        "processed": 0,
        "total": total,
        "current": None,
        "started_at": time.time(),
    }


def _set_progress(rag_db_path: str, payload: dict[str, Any]) -> None:
    key = str(Path(rag_db_path).resolve())
    _progress[key] = payload


def _clear_progress(rag_db_path: str) -> None:
    key = str(Path(rag_db_path).resolve())
    _progress.pop(key, None)


# ---------------------------------------------------------------------------
# Thread pool for the synchronous TextRAG.index_single_pdf calls.
# A single worker is fine — concurrent indexing on the same DB is
# serialized by the per-DB lock above, and we want CPU/memory pressure
# from sentence-transformers to be bounded. Across different KBs, we
# rely on the lock keys differing to allow parallelism (still bounded
# to max_workers).
# ---------------------------------------------------------------------------
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="kb-indexer")


def shutdown(*, wait: bool = True) -> None:
    """
    Shut down the module-level indexing thread pool.
    """
    _executor.shutdown(wait=wait)


# ---------------------------------------------------------------------------
# Provider gate: same logic as build_rag._resolve_llm_config, but only
# the "do we have any credentials?" decision. Returns False when nothing
# is configured, in which case we tell TextRAG to skip the LLM step.
# ---------------------------------------------------------------------------


def _parse_backend_model() -> tuple[str | None, str | None]:
    """
    Parse VISTA_BACKEND_MODEL (the canonical chat-agent config in
    .env.sample) into (provider, model). Returns (None, None) when
    unset or malformed.

    Examples:
        "azure:gpt-5"            -> ("azure", "gpt-5")
        "openai:claude-sonnet"   -> ("openai", "claude-sonnet")
        ""                       -> (None, None)
        "gpt-4o-mini"            -> (None, None)  # no provider prefix

    This exists because VISTA_BACKEND_MODEL is the documented source of
    truth for which model the chat agent uses, but until now the
    citation extractor had its own parallel config (AZURE_OPENAI_-
    DEPLOYMENT_NAME / OPENAI_MODEL) that .env.sample never set. Users
    following .env.sample to the letter ended up with citation
    extraction silently disabled. Falling back to VISTA_BACKEND_MODEL
    here keeps the two code paths in sync without forcing users to
    duplicate config.
    """
    bm = (os.environ.get("VISTA_BACKEND_MODEL") or "").strip()
    if ":" not in bm:
        return (None, None)
    provider, _, model = bm.partition(":")
    provider = provider.strip().lower()
    model = model.strip()
    if not provider or not model:
        return (None, None)
    return (provider, model)


def _resolved_azure_deployment() -> str:
    """
    Azure deployment name with VISTA_BACKEND_MODEL fallback. Explicit
    AZURE_OPENAI_DEPLOYMENT_NAME wins (back-compat); otherwise pull
    from VISTA_BACKEND_MODEL when it's of the form "azure:<dep>".
    """
    explicit = os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME")
    if explicit:
        return explicit
    provider, model = _parse_backend_model()
    if provider == "azure" and model:
        return model
    return ""


def _resolved_openai_model() -> str:
    """
    OpenAI-compatible model name with VISTA_BACKEND_MODEL fallback.
    Explicit OPENAI_MODEL wins; otherwise pull from VISTA_BACKEND_MODEL
    when it's of the form "openai:<model>". Final fallback is the same
    gpt-4o-mini default build_rag has always used.
    """
    explicit = os.environ.get("OPENAI_MODEL")
    if explicit:
        return explicit
    provider, model = _parse_backend_model()
    if provider == "openai" and model:
        return model
    return "gpt-4o-mini"


def has_llm_credentials() -> bool:
    azure_ok = bool(
        os.environ.get("AZURE_OPENAI_ENDPOINT")
        and (os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"))
        and _resolved_azure_deployment()
    )
    openai_ok = bool(os.environ.get("OPENAI_API_KEY"))
    legacy_ok = bool(
        os.environ.get("ENDPOINT_URL")
        and os.environ.get("DEPLOYMENT_NAME")
        and (os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    )
    return azure_ok or openai_ok or legacy_ok


def _describe_llm_target() -> tuple[str, str]:
    """
    Predict which (provider, model) `build_rag._resolve_llm_config` will
    pick, without actually instantiating an HTTP client. Used purely for
    logging at indexer startup so the user can verify the env config is
    what they expect.

    Mirrors `_resolve_llm_config`'s decision tree. If the resolution
    diverges from build_rag, fix this helper to match — both reading
    the same env vars in the same order is the contract.
    """
    azure_dep = _resolved_azure_deployment()
    if (
        os.environ.get("AZURE_OPENAI_ENDPOINT")
        and (os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"))
        and azure_dep
    ):
        return ("azure", azure_dep)
    if (
        os.environ.get("ENDPOINT_URL")
        and os.environ.get("DEPLOYMENT_NAME")
        and (os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    ):
        return ("azure-legacy", os.environ["DEPLOYMENT_NAME"])
    base_url = os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    return (f"openai @ {base_url}", _resolved_openai_model())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def index_publications(
    rag_db_path: str,
    pdfs_dir: str,
    filenames: list[str],
    *,
    extract_citations: bool | None = None,
    force_reindex: bool = False,
) -> list[dict[str, Any]]:
    """
    Index a list of PDFs into the rag_db at `rag_db_path`. PDFs are
    resolved against `pdfs_dir` (relative paths allowed; `..` is rejected).
    Returns a list of result dicts shaped like
    `TextRAG.index_single_pdf`:

        {
          "filename": "...",
          "status": "indexed" | "skipped" | "failed",
          "error": str | None,
          "chunk_count": int,
          "citation": dict | None,
          "citation_status": "extracted" | "failed" | "disabled" | "skipped",
        }

    `extract_citations`: defaults to whether any LLM credentials are
    configured. The text-chunk indexing pipeline runs either way.

    Updates the per-rag_db progress dict throughout. The caller is
    expected to hold the per-KB orchestration in the API layer (e.g.
    flipping the publications' index_status before/after). This
    function only owns the chroma side.
    """
    if extract_citations is None:
        extract_citations = has_llm_credentials()

    # Log this loud and clear at the start of every run. If the user
    # is expecting citation metadata and isn't seeing it, the very
    # first thing they need to know is whether the indexer is even
    # going to try — and which env vars dictated that decision.
    #
    # We log the env-var SHAPE (key present/absent + length) rather
    # than values, so the user can verify the actual environment the
    # indexer sees without us leaking secrets. Length-of-zero vs
    # length-of-N tells you "set but empty" vs "set with a real value".
    def _env_shape(name: str) -> str:
        v = os.environ.get(name)
        if v is None:
            return f"{name}=<unset>"
        if not v:
            return f"{name}=<empty>"
        return f"{name}=<set,len={len(v)}>"

    env_snapshot = ", ".join(
        _env_shape(n)
        for n in (
            "VISTA_BACKEND_MODEL",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_MODEL",
            "AZURE_OPENAI_ENDPOINT",
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_DEPLOYMENT_NAME",
            "ENDPOINT_URL",
            "DEPLOYMENT_NAME",
        )
    )
    logger.warning(  # WARNING level so it's visible even with filtered loggers
        "Indexer env snapshot for citation creds: %s",
        env_snapshot,
    )

    if extract_citations:
        # Surface the resolved provider/model so the user can verify
        # the LLM call is going where they expect (e.g. that
        # OPENAI_MODEL is actually set, not silently defaulting to
        # gpt-4o-mini against an endpoint that doesn't host it).
        provider, model = _describe_llm_target()
        logger.warning(  # WARNING so the line stands out in long indexer logs
            "Citation extraction: ENABLED (provider=%s model=%s). "
            "Each PDF will trigger an LLM call to extract title/authors/"
            "journal/year/DOI/abstract; watch for '→ LLM request' and "
            "'← LLM response' lines below.",
            provider,
            model,
        )
    else:
        # Tell the user exactly which env var combination would enable
        # it. The previous behavior — silently skipping citation when
        # creds were absent — left users wondering for 20 minutes why
        # nothing was happening.
        logger.warning(
            "Citation extraction: DISABLED — has_llm_credentials() returned False. "
            "See the env snapshot above. To enable, ensure one of these "
            "combinations is set in the backend process's environment "
            "(check your .env file is at the repo root or in backend/, "
            "and that the values are not empty strings): "
            "(AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY + "
            "AZURE_OPENAI_DEPLOYMENT_NAME), or OPENAI_API_KEY "
            "(with optional OPENAI_BASE_URL and OPENAI_MODEL). "
            'VISTA_BACKEND_MODEL="azure:<deployment>" or '
            '"openai:<model>" is also accepted as a fallback for the '
            "deployment/model name when the explicit env var is unset.",
        )

    # Defense-in-depth: paths must stay under pdfs_dir (no traversal).
    pdfs_dir_p = Path(pdfs_dir).resolve()
    cleaned: list[str] = []
    for f in filenames:
        if not f:
            continue
        rel = Path(f)
        if ".." in rel.parts or rel.name in ("", ".", ".."):
            continue
        pdf_path = (pdfs_dir_p / rel).resolve()
        if not pdf_path.is_relative_to(pdfs_dir_p):
            continue
        cleaned.append(str(rel).replace("\\", "/"))
    if not cleaned:
        return []
    rag_db_p = Path(rag_db_path).resolve()
    rag_db_p.mkdir(parents=True, exist_ok=True)

    started_at = time.time()
    _set_progress(
        rag_db_path,
        {
            "phase": "loading_model",
            "processed": 0,
            "total": len(cleaned),
            "current": None,
            "started_at": started_at,
        },
    )

    lock = _db_lock(rag_db_path)
    async with lock:
        loop = asyncio.get_running_loop()

        # Build TextRAG inside the executor — this triggers the slow
        # sentence-transformers import + chroma open. We cache the
        # instance by resolved rag_db_path so subsequent runs against
        # the same KB reuse the same chromadb.PersistentClient. See
        # the _text_rag_instances comment block above for why this
        # matters (avoids SQLITE_READONLY_DBMOVED on second-run writes).
        TextRAG = _get_text_rag_cls()
        cache_key = str(rag_db_p)

        def _build_rag():
            cached = _text_rag_instances.get(cache_key)
            if cached is not None:
                # Sanity check: the on-disk chroma dir must still
                # exist. If the user deleted and re-created the KB,
                # we want a fresh client against the new directory.
                # (KB delete also invalidates the cache via
                # invalidate_rag_cache below, but be defensive.)
                sqlite_file = rag_db_p / "chroma.sqlite3"
                if not sqlite_file.is_file():
                    logger.info(
                        "Discarding cached TextRAG for %s: "
                        "chroma.sqlite3 no longer on disk",
                        cache_key,
                    )
                    _text_rag_instances.pop(cache_key, None)
                else:
                    # Re-stamp mutable fields. The wrapped chromadb
                    # client and the embedding model are reused.
                    logger.info(
                        "Reusing cached TextRAG for %s",
                        cache_key,
                    )
                    cached.pdf_folder = str(pdfs_dir_p)
                    cached.extract_citations = extract_citations
                    cached.force_reindex = force_reindex
                    return cached
            logger.info("Constructing TextRAG for %s", cache_key)
            instance = TextRAG(
                pdf_folder=str(pdfs_dir_p),
                db_path=str(rag_db_p),
                extract_citations=extract_citations,
                force_reindex=False,  # we manage per-PDF dedup ourselves
            )
            _text_rag_instances[cache_key] = instance
            return instance

        try:
            rag = await loop.run_in_executor(_executor, _build_rag)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to initialize TextRAG for %s", rag_db_path)
            _clear_progress(rag_db_path)
            return [
                {
                    "filename": f,
                    "status": "failed",
                    "error": f"Failed to initialize indexer: {exc}",
                    "chunk_count": 0,
                    "citation": None,
                    "citation_status": "disabled",
                }
                for f in cleaned
            ]

        rag.force_reindex = force_reindex

        results: list[dict[str, Any]] = []
        for i, filename in enumerate(cleaned):
            pdf_path = pdfs_dir_p / filename
            if not pdf_path.is_file():
                results.append(
                    {
                        "filename": filename,
                        "status": "failed",
                        "error": f"File not found: {pdf_path}",
                        "chunk_count": 0,
                        "citation": None,
                        "citation_status": "disabled",
                    }
                )
                continue

            # Per-paper progress callback. TextRAG.index_single_pdf
            # fires this at each milestone — we mirror them into the
            # progress dict so the UI can show whether the slow LLM
            # citation step is what we're currently on, and (for the
            # embedding step) show chunk-level progress within a paper.
            def make_cb(idx: int, fname: str) -> Callable[[dict[str, Any]], None]:
                def cb(payload: dict[str, Any]) -> None:
                    progress: dict[str, Any] = {
                        "phase": "indexing",
                        "sub_phase": payload.get("phase", "indexing"),
                        "processed": idx,
                        "total": len(cleaned),
                        "current": fname,
                        "started_at": started_at,
                    }
                    # chunk-level sub-progress for the embedding step
                    chunk_processed = payload.get("chunk_processed")
                    chunk_total = payload.get("chunk_total")
                    if chunk_processed is not None and chunk_total is not None:
                        progress["chunk_processed"] = chunk_processed
                        progress["chunk_total"] = chunk_total
                    _set_progress(rag_db_path, progress)

                return cb

            # Initial "starting" update for this paper.
            _set_progress(
                rag_db_path,
                {
                    "phase": "indexing",
                    "sub_phase": "starting",
                    "processed": i,
                    "total": len(cleaned),
                    "current": filename,
                    "started_at": started_at,
                },
            )

            logger.info(
                "── Paper %d/%d: %s ──",
                i + 1,
                len(cleaned),
                filename,
            )
            try:
                res = await loop.run_in_executor(
                    _executor,
                    lambda p=pdf_path, c=make_cb(i, filename): rag.index_single_pdf(
                        p, progress_cb=c
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Indexing raised for %s", filename)
                res = {
                    "filename": filename,
                    "status": "failed",
                    "error": str(exc),
                    "chunk_count": 0,
                    "citation": None,
                    "citation_status": "failed" if extract_citations else "disabled",
                }
            # Per-paper summary so the user can scan the log and see at a
            # glance whether each PDF got chunks AND citation, or just
            # one, or neither. Citation outcome is the field most users
            # actually care about — surface it explicitly.
            cit_status = res.get("citation_status", "?")
            chunk_count = res.get("chunk_count", 0)
            cit_err = res.get("citation_error")
            if cit_status == "extracted":
                cit_summary = "citation=extracted"
            elif cit_status == "skipped":
                cit_summary = "citation=skipped (already in chroma)"
            elif cit_status == "failed":
                cit_summary = f"citation=FAILED ({cit_err or 'see logs above'})"
            elif cit_status == "disabled":
                cit_summary = "citation=disabled"
            else:
                cit_summary = f"citation={cit_status}"
            logger.info(
                "── Paper %d/%d done: status=%s chunks=%d %s",
                i + 1,
                len(cleaned),
                res.get("status", "?"),
                chunk_count,
                cit_summary,
            )
            results.append(res)

    _set_progress(
        rag_db_path,
        {
            "phase": "done",
            "processed": len(cleaned),
            "total": len(cleaned),
            "current": None,
            "started_at": started_at,
        },
    )
    # Clear shortly after to avoid the "done" state lingering forever;
    # the UI's next poll will see no progress and clear the bar.
    asyncio.create_task(_delayed_clear(rag_db_path, delay=2.0))

    return results


async def _delayed_clear(rag_db_path: str, *, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    _clear_progress(rag_db_path)
