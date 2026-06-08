"""
Hybrid (vector + BM25) retrieval merge logic for the
VISTAGuard G3 defense (Semantic Chameleon arXiv 2603.18034).

## What this module does

Given two ranked retrieval result lists -- one from the vector
index (ChromaDB), one from the BM25 index (vendored in
`bm25.py`) -- produce a single merged ranking. The merged list
demotes chunks that score well on only one of the two modalities,
which is exactly the signature of gradient-guided embedding-
poisoning attacks: the attacker optimizes the embedding so the
chunk surfaces in vector search, but the chunk's text scores near
zero in BM25 because it isn't lexical English.

## Merge strategy

Min-max normalization within each result list to [0, 1], then a
linear weighted sum::

    merged_score(c) = alpha * v_norm(c) + (1 - alpha) * b_norm(c)

where `v_norm(c)` is the min-max-normalized vector similarity for
chunk c (1.0 if c is the top vector match, 0.0 if it's the bottom)
and `b_norm(c)` is the same for BM25. Chunks present in only one
list get `0.0` for the missing modality -- they have to win
purely on the side they appeared in to land on the merged list.

`alpha = 0.5` (the spec default) gives BM25 and vector equal
weight. `alpha = 1.0` is vector-only (the legacy path). `alpha =
0.0` is BM25-only -- not useful in production but the math should
still work for tests.

## Why not RRF

Reciprocal Rank Fusion (Cormack et al., SIGIR 2009) is the other
standard hybrid-search merge. It's rank-based and scale-
invariant, which is appealing. We chose min-max linear weighting
for Phase 2 because:

- `alpha` has a clear operator-facing meaning ("how much do I
  trust BM25 vs vector?"). RRF's `k` constant is opaque.
- The spec explicitly names `alpha=0.5` as the default, which
  reads naturally as "equal weight" with linear fusion.
- For two-modality fusion at the K-values we use (5-20), linear
  and RRF give very similar rankings on benign workloads. The
  difference matters for >2 modalities, which isn't the Phase-2
  scope.

RRF can be added later as an opt-in merge strategy without
breaking the existing API.

## API shape

The merge function takes lists of `RetrievalResult` (a small
local dataclass that's a structural superset of both the
ChromaDB query response and `bm25.BM25Result`). The MCP server
caller builds these from each retrieval's native shape and hands
them in. Returning the same dataclass keeps the caller's
downstream formatting code uniform across hybrid and non-hybrid
paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class RetrievalResult:
    """
    A single retrieved chunk plus its native score from one
    modality.
    """

    chunk_id: str
    document: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    merged_score: float | None = None


# -----------------------------------------------------------------
# Normalization helpers
# -----------------------------------------------------------------


def _min_max_normalize(scores: Iterable[float]) -> list[float]:
    """
    Min-max normalize an iterable of scores to [0, 1].

    """
    scores_list = list(scores)
    n = len(scores_list)
    if n == 0:
        return []
    if n == 1:
        return [1.0]
    s_max = max(scores_list)
    s_min = min(scores_list)
    span = s_max - s_min
    if span == 0:
        return [1.0] * n
    return [(s - s_min) / span for s in scores_list]


# -----------------------------------------------------------------
# Public API
# -----------------------------------------------------------------


def merge_retrievals(
    vector_results: list[RetrievalResult],
    bm25_results: list[RetrievalResult],
    *,
    alpha: float = 0.5,
    top_k: int = 10,
) -> list[RetrievalResult]:
    """
    Merge vector + BM25 retrieval result lists into a single
    ranked list via min-max-normalized linear weighting.
    """
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(
            f"alpha must be in [0.0, 1.0] (got {alpha!r}); use "
            f"1.0 for vector-only or 0.0 for BM25-only"
        )
    if top_k <= 0:
        raise ValueError(f"top_k must be positive (got {top_k!r})")

    if not vector_results and not bm25_results:
        return []

    # Build chunk_id -> result lookup for each list.
    v_by_id: dict[str, RetrievalResult] = {}
    for r in vector_results:
        v_by_id.setdefault(r.chunk_id, r)
    b_by_id: dict[str, RetrievalResult] = {}
    for r in bm25_results:
        b_by_id.setdefault(r.chunk_id, r)

    # Normalize each list's scores in its own range.
    v_norm = _min_max_normalize(r.score for r in vector_results)
    b_norm = _min_max_normalize(r.score for r in bm25_results)
    v_norm_by_id = {
        r.chunk_id: v_norm[i] for i, r in enumerate(vector_results)
    }
    b_norm_by_id = {
        r.chunk_id: b_norm[i] for i, r in enumerate(bm25_results)
    }

    # The merged candidate set is the union of both lists' chunk IDs.
    seen_ids: set[str] = set()
    ordered_ids: list[str] = []
    for r in vector_results:
        if r.chunk_id not in seen_ids:
            seen_ids.add(r.chunk_id)
            ordered_ids.append(r.chunk_id)
    for r in bm25_results:
        if r.chunk_id not in seen_ids:
            seen_ids.add(r.chunk_id)
            ordered_ids.append(r.chunk_id)

    merged: list[tuple[int, RetrievalResult]] = []
    for tiebreaker, chunk_id in enumerate(ordered_ids):
        v_score = v_norm_by_id.get(chunk_id, 0.0)
        b_score = b_norm_by_id.get(chunk_id, 0.0)
        merged_score = alpha * v_score + (1.0 - alpha) * b_score
        # Prefer the vector-side record for document/metadata
        source = v_by_id.get(chunk_id) or b_by_id[chunk_id]
        merged.append(
            (
                tiebreaker,
                RetrievalResult(
                    chunk_id=source.chunk_id,
                    document=source.document,
                    metadata=dict(source.metadata),
                    score=source.score,
                    merged_score=merged_score,
                ),
            )
        )

    # Sort by merged_score desc, then by stable tiebreaker asc.
    merged.sort(key=lambda pair: (-(pair[1].merged_score or 0.0), pair[0]))

    return [result for _tiebreaker, result in merged[:top_k]]


# -----------------------------------------------------------------
# Module-level public API
# -----------------------------------------------------------------


__all__ = [
    "RetrievalResult",
    "merge_retrievals",
]
