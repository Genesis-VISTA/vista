"""
MCP tool for RAG-based document search over a pre-built ChromaDB vector store.

Provides a `rag_search` tool that performs semantic search over indexed PDFs
(text chunks) and returns passages with full citation metadata (title, authors,
DOI, journal, year) extracted at index time.

The MCP server discovers Knowledge Bases at startup by scanning
`settings.knowledge_bases_dir` for subdirectories that look like a built
ChromaDB store. Each KB is registered under its slug; the agent chooses
which KB to query by passing `kb_slug` to `rag_search`.

Environment variables:
    VISTA_DATA_DIR                 Root data directory. `knowledge_bases_dir`
                                    (`<data_dir>/knowledge-bases`) is derived
                                    from it. Default: ../../data
    VISTA_MCP_RAG_MODEL            SentenceTransformers model for query embeddings.
                                    Default: microsoft/harrier-oss-v1-270m
    VISTA_MCP_RAG_MODEL_REVISION   Hugging Face commit of that model to load,
                                    the one the shipped stores were embedded
                                    with. Default: see `config.py`
    VISTA_MCP_RAG_QUERY_INSTRUCTION
                                   One-sentence task description prepended to
                                    every query as `Instruct: ...\\nQuery: `.
                                    The model is instruction-tuned and its
                                    card warns that a bare query degrades
                                    retrieval. Documents are indexed without
                                    one, so changing this needs no reindex.
    VISTA_EMBED_DEVICE             Torch device for the encoder. Unset lets
                                    sentence-transformers choose (cuda, then
                                    mps, then cpu). `build_rag.py` reads the
                                    same variable when indexing.
"""
import asyncio
import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated as A, Any, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings
from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan
from mcp.types import ToolAnnotations
from sentence_transformers import SentenceTransformer

from .bm25 import BM25Okapi
from .config import settings
from .hybrid_search import RetrievalResult, merge_retrievals
from .metrics import stage as metrics_stage


# Filename of the BM25 corpus that `build_rag.py` writes alongside
# each KB's ChromaDB store. 
_BM25_CORPUS_FILENAME = "bm25_corpus.json"

# The file in a KB's folder naming the subfolder that holds its current
# store, as `<name>/rag_db`. The backend writes it when it imports a knowledge
# base: the import lands in a new folder and this file is replaced to point at
# it, so the store being searched is never written over. Kept in lockstep
# with `vista_backend.services.knowledge_base.CURRENT_STORE_FILE`.
_CURRENT_STORE_FILENAME = "CURRENT"
_STORE_DIR_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")

logger = logging.getLogger(__name__)

# Matches the slug format used by the backend's KnowledgeBaseTable.
# Lowercase letters/digits/dashes, 1–80 chars, must start and end with
# an alphanumeric character. Kept in lockstep with the regex in the
# backend's schemas module — see comment there for the rationale.
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$")

_Stamp = tuple[int, int, int, int] | None
""" What a file is right now (device, inode, size, mtime), or None if absent. """


def _stamp(path: Path) -> _Stamp:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


@dataclass
class _KbHandle:
    """Per-KB ChromaDB handles, opened on first use and reopened when replaced."""

    slug: str
    db_path: Path
    text_collection: chromadb.Collection | None
    citation_collection: chromadb.Collection | None
    bm25: BM25Okapi | None = None
    client: Any = None
    """ The store's ChromaDB client, closed when the handle is replaced or dropped. """
    bm25_stamp: _Stamp = None
    """ The BM25 corpus file's `_stamp` when the store was opened. """



# ---------------------------------------------------------------------------
# Module-level state (populated in lifespan)
# ---------------------------------------------------------------------------
_encoder: SentenceTransformer | None = None
_kbs: dict[str, _KbHandle] = {}


def _looks_like_chroma_store(path: Path) -> bool:
    """A directory is a ChromaDB persist dir iff it contains chroma.sqlite3."""
    return path.is_dir() and (path / "chroma.sqlite3").is_file()


def _store_path(kb_dir: Path) -> Path | None:
    """
    The ChromaDB store a KB's folder serves, or None when it has none.

      1. `<kb_dir>/<name>/rag_db/`, when `<kb_dir>/CURRENT` holds `<name>`:
         a knowledge base the backend imported (see `_CURRENT_STORE_FILENAME`).
         A `CURRENT` that names no store is no store: falling back would serve
         one the backend has replaced.
      2. `<kb_dir>/rag_db/` — the canonical per-KB layout shared with the
         backend.
      3. `<kb_dir>` itself when that directory is a ChromaDB store (the
         legacy single-directory layout).
    """
    pointer = kb_dir / _CURRENT_STORE_FILENAME
    if pointer.is_file():
        try:
            name = pointer.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        store = kb_dir / name / "rag_db"
        if _STORE_DIR_RE.match(name) and _looks_like_chroma_store(store):
            return store.resolve()
        return None
    nested = kb_dir / "rag_db"
    if _looks_like_chroma_store(nested):
        return nested.resolve()
    if _looks_like_chroma_store(kb_dir):
        return kb_dir.resolve()
    return None


def _discover_kb_paths() -> list[tuple[str, Path]]:
    """
    Return [(slug, chroma_path), ...] for every KB the MCP server can serve:
    each subdirectory of `settings.knowledge_bases_dir` named like a slug
    that has a store (`_store_path`).
    """
    discovered: list[tuple[str, Path]] = []

    root = settings.knowledge_bases_dir
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            slug = child.name
            if not _SLUG_RE.match(slug):
                continue
            store = _store_path(child)
            if store is not None:
                discovered.append((slug, store))

    return discovered


def _open_kb(slug: str, db_path: Path) -> _KbHandle | None:
    """Open one KB's ChromaDB collections and BM25 corpus; None if unopenable."""
    try:
        # Match build_rag.TextRAG's settings
        client = chromadb.PersistentClient(
            path=str(db_path),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
    except Exception as exc:
        logger.error("RAG: could not open ChromaDB for %s: %s", slug, exc)
        return None

    # Use get_or_create for the same reason the single-KB version did:
    # a fresh deployment with no PDFs indexed yet shouldn't log a
    # scary ERROR on every boot. Matching `hnsw:space=cosine` to what
    # build_rag.py uses so a later indexing run finds compatible
    # collections rather than re-creating them.
    # No `embedding_function` is passed, so Chroma attaches its default
    # (`ONNXMiniLM_L6_V2`), which downloads an ONNX archive from a public
    # S3 bucket. It never fires: `__call__` is the only thing that
    # downloads, and every read below passes `query_embeddings=` from
    # `_embed`. Do not add a call that omits them -- it would reach the
    # network on an offline machine and encode with the wrong model.
    try:
        text_collection = client.get_or_create_collection(
            name="text_chunks",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "RAG: %s text_chunks has %d items", slug, text_collection.count()
        )
    except Exception as exc:
        logger.error(
            "RAG: could not open text_chunks for %s: %s", slug, exc
        )
        text_collection = None

    try:
        citation_collection = client.get_or_create_collection(
            name="citations",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "RAG: %s citations has %d items",
            slug, citation_collection.count(),
        )
    except Exception as exc:
        logger.warning(
            "RAG: could not open citations for %s: %s", slug, exc
        )
        citation_collection = None

    # PALISADE G3 hybrid-retrieval defense (Semantic Chameleon
    # arXiv 2603.18034). 
    bm25_stamp = _stamp(db_path / _BM25_CORPUS_FILENAME)
    bm25 = _load_bm25_corpus(slug, db_path)

    return _KbHandle(
        slug=slug,
        db_path=db_path,
        text_collection=text_collection,
        citation_collection=citation_collection,
        bm25=bm25,
        client=client,
        bm25_stamp=bm25_stamp,
    )


def _close(handle: _KbHandle) -> None:
    """
    Release a KB's ChromaDB client. ChromaDB keeps one client per path for the
    whole process; until it is closed, a new store at the same path would be
    read through the old one, and on Windows its folder could not be removed.
    """
    if handle.client is None:
        return
    try:
        handle.client.close()
    except Exception as exc:  # noqa: BLE001 -- closing must not fail a search
        logger.warning("RAG: could not close ChromaDB for %s: %s", handle.slug, exc)
    handle.client = None


def _register_new_kbs() -> list[str]:
    """
    Open every discovered KB that isn't registered yet; return their slugs.

    Runs at startup and again whenever `rag_search` names a KB it doesn't
    know, so a KB created in the UI is searchable as soon as it is indexed,
    without restarting the server.
    """
    added = []
    for slug, db_path in _discover_kb_paths():
        if slug in _kbs:
            continue
        logger.info("RAG: opening ChromaDB for %s at %s", slug, db_path)
        handle = _open_kb(slug, db_path)
        if handle is not None:
            _kbs[slug] = handle
            added.append(slug)
    return added


def _refresh_kb(slug: str) -> None:
    """
    Bring a registered KB up to date with its folder before it is searched.

    The backend changes a KB under a running server, and never tells the
    server: an import points `CURRENT` at a new store, a deletion removes the
    folder (and a new KB may take the slug), indexing or deleting a PDF writes
    the store. An open ChromaDB client sees none of it. So each search
    compares the folder with what the handle opened -- two `stat`s and
    `CURRENT` -- and reopens the store or drops the KB.

    What marks a write is the BM25 corpus: every backend path that writes a
    store rewrites it in one step afterwards (`build_rag.write_bm25_corpus`),
    so its stamp changes, and a store deleted and built again has another
    one, or none. Not `chroma.sqlite3` itself: merely opening a store writes
    it, which the backend does each time it lists its knowledge bases.
    """
    handle = _kbs.get(slug)
    if handle is None:
        return
    store = _store_path(settings.knowledge_bases_dir / slug)
    if store is None:
        logger.info("RAG: %s no longer has a store; dropping it", slug)
        _close(_kbs.pop(slug))
        return
    corpus = _stamp(store / _BM25_CORPUS_FILENAME)
    if store != handle.db_path or corpus != handle.bm25_stamp:
        logger.info("RAG: %s's store at %s has changed; reopening", slug, store)
        _close(_kbs.pop(slug))
        reopened = _open_kb(slug, store)
        if reopened is not None:
            _kbs[slug] = reopened


@lifespan
async def app_lifespan(server):
    """Load the embedding model and open ChromaDB collections at startup."""
    global _encoder

    logger.info(
        "RAG: loading embedding model %s@%s (device=%s)",
        settings.rag_model,
        settings.rag_model_revision[:12],
        settings.embed_device or "auto",
    )
    _encoder = SentenceTransformer(
        settings.rag_model,
        revision=settings.rag_model_revision,
        device=settings.embed_device,
    )

    if not _register_new_kbs():
        logger.warning(
            "RAG: no Knowledge Bases discovered under %s; rag_search will "
            "return an error until at least one KB is indexed.",
            settings.knowledge_bases_dir,
        )

    yield  # server runs

    # Cleanup (SentenceTransformer needs no explicit close)
    _encoder = None
    for handle in _kbs.values():
        _close(handle)
    _kbs.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def query_prompt() -> str:
    """
    The `Instruct: ...\\nQuery: ` prefix every query is encoded with.

    Built per call rather than cached at import so a test can vary
    `settings.rag_query_instruction` without reloading the module.
    """
    return f"Instruct: {settings.rag_query_instruction.strip()}\nQuery: "


def _embed(text: str) -> list[float]:
    """
    Embed a single query string, prefixed with the retriever's task
    instruction.

    `settings.rag_model` is instruction-tuned and its
    `config_sentence_transformers.json` sets `default_prompt_name` to null,
    so sentence-transformers prepends nothing unless we pass `prompt`.
    Queries get it; the documents `build_rag.py` indexed do not, which is
    what the model card prescribes and why adding this needed no reindex.
    Do not reuse this function to encode documents.
    """
    assert _encoder is not None, "Embedding model not loaded"
    vec = _encoder.encode([text], prompt=query_prompt(), convert_to_numpy=True)
    return vec[0].tolist()


def _load_bm25_corpus(slug: str, db_path: Path) -> BM25Okapi | None:
    """
    Read `<db_path>/<_BM25_CORPUS_FILENAME>` and return the loaded
    BM25 index. Every failure mode is non-fatal -- we log and
    return None so the hybrid-retrieval path can degrade to
    vector-only.
    """
    corpus_path = db_path / _BM25_CORPUS_FILENAME
    if not corpus_path.exists():
        logger.info(
            "RAG: BM25 corpus %s not found for KB %r; hybrid retrieval "
            "will degrade to vector-only for this KB until "
            "`python build_rag.py` is re-run.",
            corpus_path, slug,
        )
        return None
    try:
        raw = corpus_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        bm25 = BM25Okapi.from_dict(data)
    except Exception as exc:  # noqa: BLE001 -- defensive
        logger.warning(
            "RAG: could not load BM25 corpus for KB %r from %s "
            "(%s: %s); hybrid retrieval will degrade to vector-only.",
            slug, corpus_path, type(exc).__name__, exc,
        )
        return None
    logger.info(
        "RAG: loaded BM25 corpus for KB %r (%d chunks) from %s",
        slug, len(bm25), corpus_path,
    )
    return bm25


def _resolve_kb(kb_slug: str | None) -> tuple[_KbHandle | None, str | None]:
    """
    Look up the KB handle for `kb_slug`. Returns (handle, error_message).

    A slug that isn't registered triggers one rescan first: the KB may have
    been created and indexed in the UI since the server started. One that is
    is checked against its folder first (`_refresh_kb`): it may have been
    replaced, deleted or reindexed since it was opened. Without a slug, only
    the KB that would be searched is reopened; the others need only still be
    there, for the list of choices.
    """
    if kb_slug:
        _refresh_kb(kb_slug)
    else:
        for slug in list(_kbs):
            if _store_path(settings.knowledge_bases_dir / slug) is None:
                _refresh_kb(slug)  # drops it
        if len(_kbs) == 1:
            _refresh_kb(next(iter(_kbs)))
    if not _kbs or (kb_slug and kb_slug not in _kbs):
        _register_new_kbs()

    if not _kbs:
        return None, (
            "ERROR: no Knowledge Bases are available on this MCP server. "
            f"The discovery scan under {settings.knowledge_bases_dir} "
            "found nothing usable. Please check that the backend has "
            "indexed at least one KB."
        )

    if not kb_slug:
        if len(_kbs) == 1:
            return next(iter(_kbs.values())), None
        choices = ", ".join(sorted(_kbs))
        return None, (
            "ERROR: this MCP server hosts multiple Knowledge Bases; pass "
            f"`kb_slug` as one of: {choices}."
        )

    handle = _kbs.get(kb_slug)
    if handle is None:
        choices = ", ".join(sorted(_kbs))
        return None, (
            f"ERROR: unknown kb_slug {kb_slug!r}. Available KBs: {choices}."
        )
    return handle, None


def _get_citation_for_source(
    handle: _KbHandle, filename: str
) -> Optional[dict]:
    """Look up pre-extracted citation metadata for a PDF by filename."""
    if handle.citation_collection is None:
        return None
    try:
        results = handle.citation_collection.get(
            where={"source": filename},
            include=["metadatas"],
        )
        if results["metadatas"]:
            meta = results["metadatas"][0]
            # Deserialise JSON-encoded list fields
            for field in ("authors", "keywords"):
                raw = meta.get(field)
                if raw and isinstance(raw, str):
                    try:
                        meta[field] = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        pass
            return meta
    except Exception as exc:
        logger.warning(
            "RAG: citation lookup failed for %s in %s: %s",
            filename, handle.slug, exc,
        )
    return None


def _format_citation(citation: dict | None) -> str:
    """Format a citation dict into a compact human-readable string."""
    if not citation:
        return ""

    parts: list[str] = []
    if citation.get("title"):
        parts.append(citation["title"])

    authors = citation.get("authors")
    if authors:
        if isinstance(authors, list):
            if len(authors) <= 3:
                parts.append(", ".join(authors))
            else:
                parts.append(f"{authors[0]} et al.")
        elif isinstance(authors, str) and authors:
            parts.append(authors)

    venue_bits: list[str] = []
    if citation.get("journal"):
        venue_bits.append(citation["journal"])
    if citation.get("volume"):
        venue_bits.append(f"vol. {citation['volume']}")
    if citation.get("year"):
        venue_bits.append(f"({citation['year']})")
    if venue_bits:
        parts.append(" ".join(venue_bits))

    if citation.get("doi"):
        parts.append(f"DOI: {citation['doi']}")

    return " — ".join(parts)


# ---------------------------------------------------------------------------
# FastMCP sub-application
# ---------------------------------------------------------------------------
mcp = FastMCP(name="RAG Search", lifespan=app_lifespan)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
async def rag_search(
    query: A[str, "Natural-language search query"],
    kb_slug: A[
        str | None,
        "Slug of the Knowledge Base to search. Use one of the slugs listed "
        "in this project's system prompt. Optional when exactly one KB is "
        "registered server-side.",
    ] = None,
    n_results: A[int, "Number of passages to return (1–20)"] = 5,
    hybrid: A[
        bool,
        "When True, fuse BM25 lexical retrieval with vector retrieval "
        "(Semantic Chameleon hybrid-search defense). Default False keeps "
        "the legacy vector-only behavior byte-identical to pre-PALISADE "
        "deployments. The PALISADE backend sets this transparently when "
        "the G3 hybrid retrieval setting is enabled.",
    ] = False,
    alpha: A[
        float,
        "When `hybrid=True`, the weight on the vector modality (0.0 = "
        "BM25 only, 1.0 = vector only, 0.5 = equal weight). Ignored "
        "when `hybrid=False`. Default 0.5 matches the Semantic "
        "Chameleon paper's reported configuration.",
    ] = 0.5,
) -> str:
    """
    Search an indexed literature corpus (papers, reports, technical notes)
    for passages relevant to the query, within a specific Knowledge Base.

    Use this tool for qualitative, conceptual, or literature-review
    questions such as:
      - "What corrosion challenges exist for FLiBe in reactor piping?"
      - "How is thermal conductivity of fluoride salts typically measured?"
      - "What do recent studies say about tritium management in FHRs?"

    Do NOT use this for quantitative lookups (melting points, viscosity
    values) — use run_bash with the structured JSON database for those.

    Returns passages with source filename, page number, and full citation
    (title, authors, journal, year, DOI) when available.

    ## Hybrid retrieval

    When `hybrid=True`, the tool fuses BM25 lexical retrieval with
    the existing vector retrieval (Semantic Chameleon arXiv
    2603.18034). The merged ranking demotes chunks that score
    only on one modality, which is the signature of gradient-
    guided embedding-poisoning attacks (PoisonedRAG, AgentPoison).
    See `hybrid_search.merge_retrievals` for the merge formula.

    Hybrid retrieval requires a `bm25_corpus.json` next to the
    KB's ChromaDB store. `build_rag.py` writes this at indexing
    time. When the file is missing for the queried KB, hybrid
    degrades to vector-only with a log warning -- the caller's
    `hybrid=True` is preserved as intent but the returned
    results are unchanged from the legacy path.
    """
    # In a thread: finding the KB may reopen its store or reload its BM25
    # corpus, and embedding and searching are blocking too. One search at a
    # time, as before, now off the event loop the other tools share.
    return await asyncio.to_thread(_search, query, kb_slug, n_results, hybrid, alpha)


_search_lock = threading.Lock()


def _search(
    query: str, kb_slug: str | None, n_results: int, hybrid: bool, alpha: float
) -> str:
    """`rag_search`'s work, blocking: under `_search_lock`, so `_kbs` has one writer."""
    with _search_lock:
        handle, err = _resolve_kb(kb_slug)
        if err is not None or handle is None:
            return err or "ERROR: unable to resolve a Knowledge Base."

        if handle.text_collection is None:
            return (
                f"ERROR: Knowledge Base {handle.slug!r} has no text_chunks "
                f"collection loaded ({handle.db_path}). The KB exists on disk "
                "but its index may not have been built yet."
            )

        n_results = max(1, min(n_results, 20))

        if hybrid:
            formatted = _hybrid_retrieve(handle, query, n_results, alpha)
            if formatted is not None:
                return formatted
            # Fall through to vector-only on hybrid degradation. We
            # log inside `_hybrid_retrieve` so the operator sees why.

        with metrics_stage("rag.embed", tool_name="rag_search"):
            query_embedding = _embed(query)
        with metrics_stage("rag.dense", tool_name="rag_search"):
            raw = handle.text_collection.query(
                query_embeddings=[query_embedding],
                n_results=n_results,
            )

        if not raw["documents"] or not raw["documents"][0]:
            return (
                f"No relevant passages found for the query in Knowledge Base "
                f"{handle.slug!r}."
            )

        # Build response with citations
        with metrics_stage("rag.citation", tool_name="rag_search"):
            return _format_results(
                handle,
                documents=raw["documents"][0],
                metadatas=raw["metadatas"][0],
            )


def _hybrid_retrieve(
    handle: _KbHandle,
    query: str,
    n_results: int,
    alpha: float,
) -> str | None:
    """
    Run vector + BM25 retrieval, merge, format. 
    """
    if handle.bm25 is None:
        logger.warning(
            "RAG: hybrid=True requested for KB %r but no BM25 corpus is "
            "loaded; degrading to vector-only retrieval.",
            handle.slug,
        )
        return None

    over_k = min(20, n_results * 4)

    # Vector retrieval -- ask ChromaDB for over-K candidates.
    with metrics_stage("rag.embed", tool_name="rag_search"):
        query_embedding = _embed(query)
    with metrics_stage("rag.dense", tool_name="rag_search"):
        raw = handle.text_collection.query(
            query_embeddings=[query_embedding],
            n_results=over_k,
            include=["documents", "metadatas", "distances"],
        )
    if not raw["documents"] or not raw["documents"][0]:
        return (
            f"No relevant passages found for the query in Knowledge Base "
            f"{handle.slug!r}."
        )

    vector_results = _chromadb_to_retrieval_results(raw)

    # BM25 retrieval over the same K.
    with metrics_stage("rag.bm25", tool_name="rag_search"):
        bm25_hits = handle.bm25.query(query, top_k=over_k)
    # Look up each BM25 hit's metadata from chromadb so the merged
    # output's `metadata` matches what vector-only callers expect.
    bm25_results: list[RetrievalResult] = []
    if bm25_hits:
        bm25_ids = [hit.chunk_id for hit in bm25_hits]
        try:
            bm25_meta_raw = handle.text_collection.get(
                ids=bm25_ids,
                include=["documents", "metadatas"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "RAG: BM25 metadata lookup failed for KB %r (%s); "
                "BM25 results will carry empty metadata.",
                handle.slug, exc,
            )
            bm25_meta_raw = None

        if bm25_meta_raw is not None:
            # Index the lookup by chunk_id so we don't depend on
            # the order ChromaDB returned them in.
            meta_by_id: dict[str, dict[str, Any]] = {}
            doc_by_id: dict[str, str] = {}
            for cid, doc, meta in zip(
                bm25_meta_raw.get("ids") or [],
                bm25_meta_raw.get("documents") or [],
                bm25_meta_raw.get("metadatas") or [],
            ):
                meta_by_id[str(cid)] = dict(meta or {})
                doc_by_id[str(cid)] = str(doc or "")
            for hit in bm25_hits:
                bm25_results.append(
                    RetrievalResult(
                        chunk_id=hit.chunk_id,
                        document=doc_by_id.get(hit.chunk_id, hit.document),
                        metadata=meta_by_id.get(hit.chunk_id, {}),
                        score=hit.score,
                    )
                )
        else:
            # Best-effort: use BM25's own copy of the text and
            # an empty metadata dict. 
            bm25_results = [
                RetrievalResult(
                    chunk_id=hit.chunk_id,
                    document=hit.document,
                    metadata={},
                    score=hit.score,
                )
                for hit in bm25_hits
            ]

    with metrics_stage("rag.fusion", tool_name="rag_search"):
        merged = merge_retrievals(
            vector_results=vector_results,
            bm25_results=bm25_results,
            alpha=alpha,
            top_k=n_results,
        )

    if not merged:
        return (
            f"No relevant passages found for the query in Knowledge Base "
            f"{handle.slug!r}."
        )

    with metrics_stage("rag.citation", tool_name="rag_search"):
        return _format_results(
            handle,
            documents=[r.document for r in merged],
            metadatas=[r.metadata for r in merged],
        )


def _chromadb_to_retrieval_results(
    raw: dict[str, Any],
) -> list[RetrievalResult]:
    """
    Convert ChromaDB's `query()` response into a list of
    `RetrievalResult` ranked by vector similarity (highest first).

    """
    ids = raw.get("ids", [[]])[0]
    docs = raw.get("documents", [[]])[0]
    metas = raw.get("metadatas", [[]])[0]
    distances = raw.get("distances", [[]])[0]
    results: list[RetrievalResult] = []
    for i, (cid, doc, meta) in enumerate(zip(ids, docs, metas)):
        dist = distances[i] if i < len(distances) else 0.0
        # Cosine distance -> similarity. 
        similarity = max(0.0, 1.0 - float(dist))
        results.append(
            RetrievalResult(
                chunk_id=str(cid),
                document=str(doc or ""),
                metadata=dict(meta or {}),
                score=similarity,
            )
        )
    return results


def _format_results(
    handle: _KbHandle,
    *,
    documents: list[str],
    metadatas: list[dict],
) -> str:
    """
    Build the formatted-text response shared by the vector-only
    and hybrid paths. Factored out so the two paths agree on
    citation lookup, header formatting, and the separator.
    """
    seen_sources: dict[str, Optional[dict]] = {}
    output_parts: list[str] = []

    for idx, (doc, meta) in enumerate(zip(documents, metadatas), 1):
        meta = meta or {}
        source = meta.get("source", "unknown")
        page = meta.get("page", "?")

        if source not in seen_sources:
            seen_sources[source] = _get_citation_for_source(handle, source)

        citation = seen_sources[source]
        citation_str = _format_citation(citation)

        header = f"[{idx}] {source}, page {page}"
        if citation_str:
            header += f"\n    Citation: {citation_str}"

        output_parts.append(f"{header}\n\n{doc}")

    separator = "\n\n" + "—" * 60 + "\n\n"
    return separator.join(output_parts)

