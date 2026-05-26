"""
Vendored BM25Okapi for the VISTAGuard hybrid-retrieval defense
(Semantic Chameleon arXiv 2603.18034).

## Why vendored

Adding `rank-bm25` (the standard PyPI package) would pull in
another small dependency for ~80 lines of code that BM25 actually
needs. We vendor a clean implementation here so:

- The on-disk corpus format (`bm25_corpus.json`) is owned by us
  -- a future BM25 implementation switch doesn't break stored
  indices.
- The tokenizer and the IDF computation are pinned to a single
  agreed version, so VISTAGuard's hybrid-retrieval defense
  doesn't silently shift its detection profile across MCP-server
  releases.
- The MCP server's startup cost is one fewer import and one
  fewer dep to audit.

## What BM25 does for VISTAGuard

The Semantic Chameleon defense observation: gradient-guided
embedding-poisoning attacks (PoisonedRAG, AgentPoison) optimize
chunk *embeddings* to be retrieved by a target query, but the
underlying chunk text is typically semantic gibberish -- the
optimizer cares about the embedding's coordinates, not whether
the text is readable. BM25 ranks chunks by lexical term overlap
with the query: a gradient-crafted chunk that has the "right"
embedding but contains random tokens scores near zero in BM25.

Combining BM25 with vector retrieval (this module + the merging
in `hybrid_search.py`) demotes attacker chunks toward the bottom
of the merged ranking. The taxonomy reports 38% → 0% ASR
reduction with this defense.

## What's in scope

Standard Okapi BM25 with k1=1.5, b=0.75 (the parameters tuned in
the original 1994 paper and re-validated by Manning et al.,
*Introduction to Information Retrieval*, 2008). A simple
lowercase-and-split tokenizer good enough for English scientific
text. Idempotent JSON serialization so the same corpus on disk
produces the same in-memory index across builds.

## What's out of scope

- Stemming, lemmatization, stop-word removal. These improve
  legitimate-query recall but don't help the security signal --
  a gradient-crafted nonsense chunk scores near zero with or
  without stemming. Phase 6 can add stemming if the
  benign-workload recall measurement asks for it.
- Smarter tokenization (subword, BPE). Same reasoning: the
  signal we need is "is this chunk lexical English?" and the
  default `re.findall` on `\w+` gives that.
- BM25F (multi-field BM25), BM25+, or LambdaMart re-rankers.
  Out of scope for Phase 2.

## Threat model and limits

BM25 catches *gradient-crafted text-nonsense* attacks. It does
NOT catch:

- **Coherent-text poisoning.** An attacker who writes a fluent
  English paragraph that smuggles instructions will score
  normally in BM25. The Q-LLM scan in G3 slow tier is the
  defense for that.
- **Stop-word-only attacks.** A chunk consisting only of common
  English stop words scores low in BM25 due to low IDF; the
  attacker could match the query's exact lexicon to game this.
  Adaptive-attack analysis in Phase 6 covers this case.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any


# -----------------------------------------------------------------
# Tokenizer
# -----------------------------------------------------------------

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """
    Lowercase + word-split tokenizer.
    """
    return [t.lower() for t in _TOKEN_RE.findall(text)]


# -----------------------------------------------------------------
# BM25 index
# -----------------------------------------------------------------


# Default Okapi BM25 hyperparameters. Tuned in Robertson & Walker
# (1994); standard in the IR literature.
DEFAULT_K1: float = 1.5
DEFAULT_B: float = 0.75


@dataclass
class BM25Result:
    """
    A single retrieved chunk plus its BM25 score.

    Fields:
        chunk_id: the chunk's identifier (same scheme as
            ChromaDB IDs in this codebase: `<source_stem>_text_<n>`).
        document: the raw chunk text.
        score: BM25 score; higher = better match. Unbounded by
            BM25's definition; for the typical scientific corpus
            and a 4-6 word query the score ranges roughly 0-30.
        rank: 0-indexed position in the result list (0 = top
            match). Carried separately from the score because
            downstream consumers (the hybrid-merge step) need
            both.
    """

    chunk_id: str
    document: str
    score: float
    rank: int


class BM25Okapi:
    """
    Standard Okapi BM25 index.

    Constructed empty, then populated via `add_documents`. Query
    via `query(query_text, top_k)`. JSON-serializable via
    `to_dict` / `from_dict` for the on-disk corpus format the
    MCP server loads at lifespan startup.

    The implementation pre-computes per-term IDF at corpus-build
    time. Adding documents *after* `idf` has been computed
    invalidates the index and re-derives it on next query.

    Not thread-safe. The MCP server constructs one index per KB
    at lifespan startup and reads from it concurrently -- since
    reads do not modify state, that's safe; adds happen only
    during lifespan startup which is serial.
    """

    def __init__(
        self,
        *,
        k1: float = DEFAULT_K1,
        b: float = DEFAULT_B,
    ) -> None:
        self.k1 = k1
        self.b = b
        # Per-document storage.
        self._chunk_ids: list[str] = []
        self._documents: list[str] = []
        self._tokenized: list[list[str]] = []
        self._doc_lengths: list[int] = []
        # Lazy-derived globals.
        self._idf: dict[str, float] | None = None
        self._avgdl: float | None = None

    # -----------------------------------------------------------------
    # Read-only accessors
    # -----------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._documents)

    @property
    def chunk_ids(self) -> list[str]:
        """Snapshot of indexed chunk IDs in insertion order."""
        return list(self._chunk_ids)

    # -----------------------------------------------------------------
    # Mutation (only at index-build time)
    # -----------------------------------------------------------------

    def add_documents(
        self,
        documents: list[tuple[str, str]],
        *,
        pre_tokenized: list[list[str]] | None = None,
    ) -> None:
        if pre_tokenized is not None and len(pre_tokenized) != len(documents):
            raise ValueError(
                f"pre_tokenized length {len(pre_tokenized)} "
                f"does not match documents length {len(documents)}"
            )
        for i, (chunk_id, text) in enumerate(documents):
            self._chunk_ids.append(chunk_id)
            self._documents.append(text)
            tokens = (
                pre_tokenized[i] if pre_tokenized is not None else tokenize(text)
            )
            self._tokenized.append(tokens)
            self._doc_lengths.append(len(tokens))

        # Invalidate caches; recomputed lazily on next query.
        self._idf = None
        self._avgdl = None

    # -----------------------------------------------------------------
    # Query
    # -----------------------------------------------------------------

    def query(self, query_text: str, *, top_k: int = 10) -> list[BM25Result]:
        """
        Return the top-`top_k` matches for `query_text`, ranked by
        BM25 score (descending).

        Empty corpus or empty query returns `[]`. Ties are broken
        by chunk insertion order (Python's stable sort).

        The score formula is the standard Okapi BM25:

            score(D, Q) = sum over terms t in Q:
                IDF(t) * (tf(t, D) * (k1 + 1))
                       / (tf(t, D) + k1 * (1 - b + b * (|D| / avgdl)))

        where `tf` is term frequency in the document, `|D|` is
        the document's token count, and `avgdl` is the average
        document length across the corpus. IDF uses the
        Robertson-Sparck-Jones smoothed form:

            IDF(t) = log(1 + (N - df(t) + 0.5) / (df(t) + 0.5))

        which is non-negative for all terms (avoiding the negative-
        IDF pathology when a term appears in more than half the
        corpus).
        """
        if not self._documents or not query_text:
            return []

        if self._idf is None or self._avgdl is None:
            self._compute_idf_and_avgdl()
        # The above call sets both fields; mypy / readability shim.
        assert self._idf is not None and self._avgdl is not None

        query_tokens = tokenize(query_text)
        if not query_tokens:
            return []

        scores: list[float] = []
        for doc_tokens, doc_len in zip(self._tokenized, self._doc_lengths):
            token_counts = Counter(doc_tokens)
            score = 0.0
            length_norm = 1 - self.b + self.b * (
                doc_len / self._avgdl if self._avgdl > 0 else 1.0
            )
            denom_base = self.k1 * length_norm
            for term in query_tokens:
                tf = token_counts.get(term, 0)
                if tf == 0:
                    continue
                idf = self._idf.get(term, 0.0)
                if idf == 0.0:
                    continue
                numerator = tf * (self.k1 + 1)
                denominator = tf + denom_base
                score += idf * (numerator / denominator)
            scores.append(score)

        # Sort indices by score desc, breaking ties by original order.
        indexed = list(enumerate(scores))
        indexed.sort(key=lambda pair: pair[1], reverse=True)

        results: list[BM25Result] = []
        for rank, (idx, score) in enumerate(indexed[:top_k]):
            results.append(
                BM25Result(
                    chunk_id=self._chunk_ids[idx],
                    document=self._documents[idx],
                    score=float(score),
                    rank=rank,
                )
            )
        return results

    # -----------------------------------------------------------------
    # Internal: IDF + avgdl computation
    # -----------------------------------------------------------------

    def _compute_idf_and_avgdl(self) -> None:
        n_docs = len(self._documents)
        if n_docs == 0:
            self._idf = {}
            self._avgdl = 0.0
            return

        # Document frequency per term.
        df: dict[str, int] = {}
        for doc_tokens in self._tokenized:
            for term in set(doc_tokens):
                df[term] = df.get(term, 0) + 1

        # Robertson-Sparck-Jones smoothed IDF
        idf: dict[str, float] = {}
        for term, term_df in df.items():
            idf[term] = math.log(
                1.0 + (n_docs - term_df + 0.5) / (term_df + 0.5)
            )
        self._idf = idf

        # Average document length.
        total = sum(self._doc_lengths)
        self._avgdl = total / n_docs

    # -----------------------------------------------------------------
    # JSON serialization
    # -----------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """
        Return a JSON-serializable dict representing the corpus.

        Format (v1)::

            {
              "version": 1,
              "k1": 1.5,
              "b": 0.75,
              "documents": [
                {"id": "<chunk_id>", "text": "<chunk text>",
                 "tokens": ["...", "..."]},
                ...
              ]
            }

        IDF and avgdl are NOT persisted: they're cheap to
        recompute and persisting them would lock us into a
        single hyperparameter choice on disk. The loader
        reconstructs them lazily.
        """
        return {
            "version": 1,
            "k1": self.k1,
            "b": self.b,
            "documents": [
                {"id": cid, "text": text, "tokens": list(tokens)}
                for cid, text, tokens in zip(
                    self._chunk_ids, self._documents, self._tokenized
                )
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BM25Okapi":
        """
        Load a BM25 index from its JSON-serializable dict.

        """
        version = data.get("version")
        if version != 1:
            raise ValueError(
                f"unsupported BM25 corpus version: {version!r} (expected 1)"
            )
        docs_raw = data.get("documents")
        if not isinstance(docs_raw, list):
            raise ValueError(
                "BM25 corpus 'documents' must be a list "
                f"(got {type(docs_raw).__name__})"
            )

        index = cls(
            k1=float(data.get("k1", DEFAULT_K1)),
            b=float(data.get("b", DEFAULT_B)),
        )
        documents: list[tuple[str, str]] = []
        pre_tokenized: list[list[str]] = []
        for entry in docs_raw:
            chunk_id = str(entry["id"])
            text = str(entry["text"])
            tokens = entry.get("tokens")
            if isinstance(tokens, list):
                pre_tokenized.append([str(t) for t in tokens])
            else:
                pre_tokenized.append(tokenize(text))
            documents.append((chunk_id, text))
        index.add_documents(documents, pre_tokenized=pre_tokenized)
        return index


# -----------------------------------------------------------------
# Module-level public API
# -----------------------------------------------------------------


__all__ = [
    "BM25Okapi",
    "BM25Result",
    "DEFAULT_B",
    "DEFAULT_K1",
    "tokenize",
]
