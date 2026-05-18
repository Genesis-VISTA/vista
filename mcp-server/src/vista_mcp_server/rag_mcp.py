"""
MCP tool for RAG-based document search over a pre-built ChromaDB vector store.

Provides a `rag_search` tool that performs semantic search over indexed PDFs
(text chunks) and returns passages with full citation metadata (title, authors,
DOI, journal, year) extracted at index time.

The tool expects a pre-built ChromaDB database (created by the TextRAG indexing
pipeline in `text_rag.py`).  It loads the embedding model once at startup and
keeps the ChromaDB client open for the lifetime of the MCP server.

Environment variables:
    VISTA_MCP_RAG_DB_PATH    Path to the ChromaDB database directory.
                              Default: ../rag_db  (relative to cwd)
    VISTA_MCP_RAG_MODEL      SentenceTransformers model for query embeddings.
                              Default: google/embeddinggemma-300m
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated as A, Any, Optional

import chromadb
from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan
from mcp.types import ToolAnnotations
from sentence_transformers import SentenceTransformer

from .config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state (populated in lifespan)
# ---------------------------------------------------------------------------
_encoder: SentenceTransformer | None = None
_text_collection: chromadb.Collection | None = None
_citation_collection: chromadb.Collection | None = None


@lifespan
async def app_lifespan(server):
    """Load the embedding model and open ChromaDB collections at startup."""
    global _encoder, _text_collection, _citation_collection

    db_path = str(settings.rag_db_path)
    logger.info("RAG: loading embedding model %s", settings.rag_model)
    _encoder = SentenceTransformer(settings.rag_model, device="cpu")

    logger.info("RAG: opening ChromaDB at %s", db_path)
    client = chromadb.PersistentClient(path=db_path)

    try:
        _text_collection = client.get_collection("text_chunks")
        logger.info(
            "RAG: text_chunks collection has %d items", _text_collection.count()
        )
    except Exception as exc:
        logger.error("RAG: could not open text_chunks collection: %s", exc)
        _text_collection = None

    try:
        _citation_collection = client.get_collection("citations")
        logger.info(
            "RAG: citations collection has %d items", _citation_collection.count()
        )
    except Exception as exc:
        logger.warning("RAG: could not open citations collection: %s", exc)
        _citation_collection = None

    yield  # server runs

    # Cleanup (SentenceTransformer and ChromaDB don't need explicit close)
    _encoder = None
    _text_collection = None
    _citation_collection = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _embed(text: str) -> list[float]:
    """Embed a single query string."""
    assert _encoder is not None, "Embedding model not loaded"
    vec = _encoder.encode([text], convert_to_numpy=True)
    return vec[0].tolist()


def _get_citation_for_source(filename: str) -> Optional[dict]:
    """Look up pre-extracted citation metadata for a PDF by filename."""
    if _citation_collection is None:
        return None
    try:
        results = _citation_collection.get(
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
        logger.warning("RAG: citation lookup failed for %s: %s", filename, exc)
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
    query: A[str, "Natural-language search query over the molten salt literature corpus"],
    n_results: A[int, "Number of passages to return (1–20)"] = 5,
) -> str:
    """
    Search the indexed literature corpus (papers, reports, technical notes)
    for passages relevant to the query.

    Use this tool for qualitative, conceptual, or literature-review questions
    such as:
      - "What corrosion challenges exist for FLiBe in reactor piping?"
      - "How is thermal conductivity of fluoride salts typically measured?"
      - "What do recent studies say about tritium management in FHRs?"

    Do NOT use this for quantitative lookups (melting points, viscosity values)
    — use run_bash with the structured JSON database for those.

    Returns passages with source filename, page number, and full citation
    (title, authors, journal, year, DOI) when available.
    """
    if _text_collection is None:
        return (
            "ERROR: RAG database is not available. "
            "The text_chunks collection could not be loaded from "
            f"{settings.rag_db_path}. Please check that the database has been built."
        )

    n_results = max(1, min(n_results, 20))

    query_embedding = _embed(query)
    raw = _text_collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
    )

    if not raw["documents"] or not raw["documents"][0]:
        return "No relevant passages found for the query."

    # Build response with citations
    seen_sources: dict[str, Optional[dict]] = {}
    output_parts: list[str] = []

    for idx, (doc, meta) in enumerate(
        zip(raw["documents"][0], raw["metadatas"][0]), 1
    ):
        source = meta.get("source", "unknown")
        page = meta.get("page", "?")

        # Lazy-load citation for each unique source
        if source not in seen_sources:
            seen_sources[source] = _get_citation_for_source(source)

        citation = seen_sources[source]
        citation_str = _format_citation(citation)

        header = f"[{idx}] {source}, page {page}"
        if citation_str:
            header += f"\n    Citation: {citation_str}"

        output_parts.append(f"{header}\n\n{doc}")

    separator = "\n\n" + "—" * 60 + "\n\n"
    return separator.join(output_parts)
