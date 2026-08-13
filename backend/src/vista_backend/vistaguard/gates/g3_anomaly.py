"""
G3 embedding-cluster anomaly detector (Chen et al. NeurIPS 2024 --
AgentPoison; Zou et al. arXiv 2402.07867 -- PoisonedRAG).

This module is the *statistical* arm of G3's RAG defense. It
consumes the embeddings of retrieved chunks (already produced by
the RAG layer in `mcp-server/src/vista_mcp_server/rag_mcp.py`,
which uses ChromaDB) and flags chunks whose pairwise-distance
profile is anomalous relative to the rest of the retrieval batch.

## What we catch

Two attack signatures share a structural fingerprint in
embedding space:

- **AgentPoison's constrained-optimization triggers.** The
  attacker optimizes a single chunk's embedding to be retrieved
  by a target query. The optimized embedding is a lone point
  far from the natural distribution of corpus chunks. In a
  retrieval batch, this lone outlier has an anomalously high
  mean pairwise distance to its siblings.

- **PoisonedRAG's tight injected cluster.** The attacker injects
  N chunks with mutually similar embeddings into a corpus. If a
  *minority* of the retrieval batch is from this cluster, those
  chunks have an anomalously low mean pairwise distance (they're
  closer to each other than to the legitimate chunks). If the
  poisoned cluster dominates the batch, this detector cannot
  distinguish poisoned from legitimate -- the cluster IS the
  batch's average. Documented limit; the slow-tier Q-LLM scan
  and (eventually) a corpus-reference baseline cover that gap.

Both signatures collapse onto the same primitive: per-chunk
z-score of mean pairwise distance, with a symmetric threshold so
positive and negative deviations both flag. Phase-2 ships exactly
that primitive.

## What we explicitly do NOT catch

- **Natural-norm crafted embeddings.** An attacker who crafts a
  poisoned chunk whose embedding lives inside the natural
  distribution (matching its norm and direction statistics) is
  invisible to this detector by construction. The semantic-
  entropy probe (a separate Phase-2 / Phase-6 issue) is the
  intended coverage for that gap. A test in `test_g3_anomaly.py`
  exercises this case and documents it as expected behavior.

- **Single-chunk batches.** If `rag_search` returns one chunk
  there is no pairwise distance to compute. The detector returns
  a benign "batch too small" result; the deterministic G3 fast-
  tier (corpus allow-list, query-injection regex, manifest hash)
  still runs on the same chunk.

- **All-identical batches.** When every chunk in the batch is
  identical, the distance distribution has zero standard
  deviation and z-scores are undefined. The detector returns a
  benign result and a reason string documenting the math.

## Algorithm

Given a (n, d) embedding matrix `E` with n chunks:

1. L2-normalize each row so cosine distances are comparable. This
   defends against an attacker who can inflate an embedding's
   norm to make it look "far" in raw Euclidean distance.
2. Compute the (n, n) cosine-distance matrix
   `D = 1 - E_normalized @ E_normalized.T`.
3. Set the diagonal to 0 (a chunk is always distance 0 from
   itself) and compute each row's *mean over the other n-1
   chunks*: `m_i = sum(D[i]) / (n - 1)`.
4. Z-score across the batch: `z_i = (m_i - mean(m)) / std(m)`.
5. Flag indices where `|z_i| > z_threshold`.

The whole pipeline is O(n^2 * d) for the embedding matrix
multiply, which is the dominant cost. For typical RAG retrieval
sizes (n in 5..20, d in 384..1536) this is sub-millisecond and
not on the hot path of the agent loop.

## API surface

The detector is a pure function -- no I/O, no global state,
deterministic for the same input. The G3 gate (a follow-on Phase-2
issue) calls it with the embeddings ChromaDB returned, the
configured threshold from `VistaGuardSettings`, and routes the
result through the same `GateDecision` -> incident plumbing as
the other G3 checks.

`AnomalyResult` is a frozen dataclass so consumers stashing it on
audit records can't mutate it; per-chunk z-scores are exposed for
the provenance trail.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


logger = logging.getLogger(__name__)


# -----------------------------------------------------------------
# Result dataclass
# -----------------------------------------------------------------


@dataclass(frozen=True)
class AnomalyResult:
    """
    Outcome of `detect_embedding_anomalies` on a single retrieval
    batch.

    """

    flagged_indices: tuple[int, ...]
    z_scores: tuple[float, ...]
    mean_pairwise_distances: tuple[float, ...]
    z_threshold: float
    reason: str
    ran: bool


# -----------------------------------------------------------------
# Public API
# -----------------------------------------------------------------


def detect_embedding_anomalies(
    embeddings: Sequence[Sequence[float]] | np.ndarray,
    *,
    z_threshold: float = 3.0,
    min_batch_size: int = 3,
) -> AnomalyResult:
    """
    Flag chunks in a retrieval batch whose mean pairwise
    embedding-distance is statistically anomalous.
    """
    if z_threshold <= 0:
        raise ValueError(
            f"z_threshold must be positive (got {z_threshold!r}); "
            "use a large value like 100.0 to effectively disable flagging"
        )

    array = _coerce_embeddings(embeddings)
    n = array.shape[0]

    # ----- Below-min-size short-circuit -----
    if n < min_batch_size:
        return AnomalyResult(
            flagged_indices=(),
            z_scores=tuple(math.nan for _ in range(n)),
            mean_pairwise_distances=tuple(math.nan for _ in range(n)),
            z_threshold=z_threshold,
            reason=(
                f"batch too small (n={n} < min_batch_size={min_batch_size}); "
                f"no anomaly detection"
            ),
            ran=False,
        )

    # ----- Pairwise cosine distance matrix -----
    normalized = _l2_normalize(array)
    similarity = normalized @ normalized.T
    np.clip(similarity, -1.0, 1.0, out=similarity)
    distances = 1.0 - similarity
    np.fill_diagonal(distances, 0.0)

    # ----- Per-chunk mean pairwise distance to other chunks -----
    mean_distances = distances.sum(axis=1) / (n - 1)

    # ----- Z-score across the batch -----
    distribution_mean = float(np.mean(mean_distances))
    distribution_std = float(np.std(mean_distances, ddof=0))

    if not math.isfinite(distribution_std) or distribution_std == 0.0:
        # All chunks have identical mean distance
        return AnomalyResult(
            flagged_indices=(),
            z_scores=tuple(math.nan for _ in range(n)),
            mean_pairwise_distances=tuple(float(m) for m in mean_distances),
            z_threshold=z_threshold,
            reason=(
                "distance-distribution std is zero "
                "(typically all-identical embeddings); no z-score signal"
            ),
            ran=False,
        )

    z_scores_arr = (mean_distances - distribution_mean) / distribution_std

    # `|z| > threshold` -- symmetric so a tight minority cluster
    # (negative z) is flagged just as a lone outlier (positive z)
    # is. See module docstring on why both directions matter.
    flagged = tuple(
        int(idx) for idx, z in enumerate(z_scores_arr) if abs(z) > z_threshold
    )

    if flagged:
        reason = (
            f"flagged {len(flagged)} anomalous chunk(s) at z_threshold={z_threshold} "
            f"(indices={list(flagged)}, "
            f"mean_distance={distribution_mean:.4f}, "
            f"std={distribution_std:.4f})"
        )
        logger.warning("VISTAGuard G3 anomaly: %s", reason)
    else:
        reason = (
            f"clean batch (n={n}, mean_distance={distribution_mean:.4f}, "
            f"std={distribution_std:.4f}, max|z|={max(abs(float(z)) for z in z_scores_arr):.2f})"
        )

    return AnomalyResult(
        flagged_indices=flagged,
        z_scores=tuple(float(z) for z in z_scores_arr),
        mean_pairwise_distances=tuple(float(m) for m in mean_distances),
        z_threshold=z_threshold,
        reason=reason,
        ran=True,
    )


# -----------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------


def _coerce_embeddings(
    embeddings: Sequence[Sequence[float]] | np.ndarray,
) -> np.ndarray:
    """
    Accept the various input shapes the G3 gate might hand us
    (numpy ndarray from ChromaDB, list-of-lists from the JSON
    wire format) and return a (n, d) float64 ndarray.
    """
    try:
        array = np.asarray(embeddings, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"embeddings must be a 2-D array-like of floats "
            f"(got {type(embeddings).__name__}: {exc})"
        ) from exc

    if array.ndim == 0:
        raise ValueError(
            f"embeddings must be a 2-D array-like; got scalar of shape {array.shape}"
        )
    if array.ndim == 1:
        # If the caller passed a single embedding by accident,
        # tell them how to fix it.
        raise ValueError(
            f"embeddings must be 2-D (n_chunks, embedding_dim); "
            f"got 1-D of length {array.shape[0]}. Did you mean to wrap "
            f"in a list, or call with a single-chunk batch as "
            f"`[[ ... ]]`?"
        )
    if array.ndim > 2:
        raise ValueError(
            f"embeddings must be 2-D; got {array.ndim}-D with shape {array.shape}"
        )
    if not np.issubdtype(array.dtype, np.floating):
        raise ValueError(f"embeddings must be numeric; got dtype {array.dtype}")

    return array


def _l2_normalize(array: np.ndarray) -> np.ndarray:
    """
    L2-normalize each row to unit length so cosine distances are
    comparable across embeddings of different magnitudes.
    """
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    safe_norms = np.where(norms == 0.0, 1.0, norms)
    return array / safe_norms


# -----------------------------------------------------------------
# Module-level public API
# -----------------------------------------------------------------


__all__ = [
    "AnomalyResult",
    "detect_embedding_anomalies",
]
