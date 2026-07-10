"""
G3 hybrid (BM25 + vector) retrieval -- backend side.

This module is the *backend* half of the hybrid-retrieval defense
(Semantic Chameleon arXiv 2603.18034); the actual BM25 + vector
fusion runs on the MCP server (see
`mcp-server/src/vista_mcp_server/hybrid_search.py`). The backend's
responsibilities are:

1. Decide whether a given `rag_search` tool call should be
   hybridized, given the VISTAGuard settings.
2. Inject `hybrid=True` and `alpha=<configured>` into the tool
   call's args before it reaches the MCP server.
3. Carry a *mirror* of the merge math so tests can pin the merge
   formula without spinning up the MCP server.

The mirror is intentional duplication: the MCP server runs in a
separate package with its own venv and no production import path
back to `vista_backend`. Pinning the merge math here lets the
backend's test suite catch a future drift in the merge formula
without needing an MCP-server integration harness. The drift is
caught by `test_g3_hybrid.py::test_merge_matches_mcp_server_module`,
which imports both copies and runs them on the same inputs.

## When the gate fires

In Phase 2 of VISTAGuard, G3 is not yet wired into the sidecar's
`process_tool_call` (a separate follow-on issue covers the
wiring). For now this module ships two pure functions and the
duplicated merge primitive; the follow-on issue will consume them
from the G3 gate's `_check_fast_when_enabled`.

The follow-on wiring will be approximately::

    if (
        settings.vistaguard.g3_enabled
        and settings.vistaguard.g3_hybrid_retrieval
        and tool_name == "rag_search"
    ):
        args = inject_hybrid_args(args, alpha=settings.vistaguard.g3_hybrid_alpha)

That's deliberately at the sidecar's tool-call interception layer
(not at the LLM prompt layer) so the model never has to know
hybridization happened. From the agent's perspective it called
`rag_search`; the gate quietly upgrades the call.

## Why mirror the merge?

Two reasons that survive the "but it's duplicated code" smell test:

- **Test-pinning the formula.** The math is small (~30 lines of
  numpy-free linear algebra). Backend tests can assert specific
  merged orderings on synthetic inputs without running the MCP
  server's fastmcp stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from ..config import VistaGuardSettings


# -----------------------------------------------------------------
# Decision helpers
# -----------------------------------------------------------------


def should_inject_hybrid(
    settings: VistaGuardSettings,
    tool_name: str,
) -> bool:
    """
    True iff the gate should inject `hybrid=True` into this tool
    call
    """
    return (
        settings.g3_enabled
        and settings.g3_hybrid_retrieval
        and tool_name == "rag_search"
    )


def inject_hybrid_args(
    args: dict[str, Any],
    *,
    alpha: float,
) -> dict[str, Any]:
    """
    Return a new args dict with `hybrid=True` and the configured
    `alpha`.
    """
    return {**args, "hybrid": True, "alpha": alpha}


# -----------------------------------------------------------------
# Mirror of the merge math (see module docstring)
# -----------------------------------------------------------------


@dataclass(frozen=True)
class MirroredRetrievalResult:
    """
    Backend-side mirror of the MCP server's
    `vista_mcp_server.hybrid_search.RetrievalResult`.
    """

    chunk_id: str
    document: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    merged_score: float | None = None


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


def merge_retrievals(
    vector_results: list[MirroredRetrievalResult],
    bm25_results: list[MirroredRetrievalResult],
    *,
    alpha: float = 0.5,
    top_k: int = 10,
) -> list[MirroredRetrievalResult]:
    """
    Merge two ranked retrieval result lists into one via min-max-
    normalized linear weighted fusion.

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

    v_by_id: dict[str, MirroredRetrievalResult] = {}
    for r in vector_results:
        v_by_id.setdefault(r.chunk_id, r)
    b_by_id: dict[str, MirroredRetrievalResult] = {}
    for r in bm25_results:
        b_by_id.setdefault(r.chunk_id, r)

    v_norm = _min_max_normalize(r.score for r in vector_results)
    b_norm = _min_max_normalize(r.score for r in bm25_results)
    v_norm_by_id = {r.chunk_id: v_norm[i] for i, r in enumerate(vector_results)}
    b_norm_by_id = {r.chunk_id: b_norm[i] for i, r in enumerate(bm25_results)}

    # Stable union order: vector first, then BM25-only.
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

    merged: list[tuple[int, MirroredRetrievalResult]] = []
    for tiebreaker, chunk_id in enumerate(ordered_ids):
        v_score = v_norm_by_id.get(chunk_id, 0.0)
        b_score = b_norm_by_id.get(chunk_id, 0.0)
        merged_score = alpha * v_score + (1.0 - alpha) * b_score
        source = v_by_id.get(chunk_id) or b_by_id[chunk_id]
        merged.append(
            (
                tiebreaker,
                MirroredRetrievalResult(
                    chunk_id=source.chunk_id,
                    document=source.document,
                    metadata=dict(source.metadata),
                    score=source.score,
                    merged_score=merged_score,
                ),
            )
        )

    merged.sort(key=lambda pair: (-(pair[1].merged_score or 0.0), pair[0]))
    return [result for _tiebreaker, result in merged[:top_k]]


# -----------------------------------------------------------------
# Module-level public API
# -----------------------------------------------------------------


__all__ = [
    "MirroredRetrievalResult",
    "inject_hybrid_args",
    "merge_retrievals",
    "should_inject_hybrid",
]
