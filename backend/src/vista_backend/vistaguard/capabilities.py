"""
Capability tagging for VISTAGuard.

This module implements the *capability registry* described in the
VISTAGuard architecture: every value that crosses a boundary (B1-B7)
carries a `CapabilityTag` describing its provenance, sensitivity, and
trust. Tags live in a per-session `CapabilityRegistry` that gates
consult to make policy decisions.

The structural argument that VISTAGuard makes about adaptive attackers
rests on this module: capabilities are stored in the
interpreter, not in the LLM's context window. A compromised Q-LLM can
fail to *detect* that a chunk contains an instruction, but it cannot
*remove* the chunk's `taint=True` tag from the registry, because
nothing the LLM produces is ever a registry write. All registry writes
go through deliberate `tag(...)` or `propagate(...)` calls in
VISTAGuard gate code.

To enforce this in practice, `CapabilityTag` is frozen: derived tags
are produced by calling `propagate(...)`, which returns and registers
a new tag computed from inputs. Direct field mutation is not
supported.

## Composition semantics

When a value is derived from multiple inputs (e.g., the agent passes
two tool returns into a third tool), the derived value's capability
must be at least as restrictive as any input. We use the lattice
join:

- `sensitivity`: maximum along the order
  OPEN < INTERNAL < CUI < EXPORT_CONTROLLED.
- `taint`: logical OR (any tainted input -> tainted output).
- `dual_use`: maximum along a precedence order
  NONE < CYBER < CHEM < BIO < NUCLEAR. A future enhancement may
  change this to `frozenset[DualUseMarker]` so that a value derived
  from BIO and CHEM inputs carries both markers without collapsing;
  changing this is API-compatible because callers go through
  `propagate(...)`.
- `provenance_chain`: appended with `f"{sink}<-({source_ids})"` so
  the audit trail records both the originating sources and the
  operation that combined them.
- `metadata`: shallow union of input metadata (later inputs win on
  key collisions); the merging policy is intentionally simple because
  metadata is informational, not enforcement-bearing.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from enum import Enum
from fnmatch import fnmatch
from typing import Any


class SensitivityTier(str, Enum):
    """
    DOE sensitivity tier for a value.

    Ordered: OPEN < INTERNAL < CUI < EXPORT_CONTROLLED. The numeric
    order is used by `CapabilityRegistry.propagate(...)` to compute
    the most-restrictive tier across inputs.
    """

    OPEN = "open"
    INTERNAL = "internal"
    CUI = "cui"
    EXPORT_CONTROLLED = "export_controlled"

    @property
    def _rank(self) -> int:
        return _SENSITIVITY_RANK[self]


# Module-level ranks keep `SensitivityTier._rank` cheap without
# embedding rank state in the enum members themselves.
_SENSITIVITY_RANK = {
    SensitivityTier.OPEN: 0,
    SensitivityTier.INTERNAL: 1,
    SensitivityTier.CUI: 2,
    SensitivityTier.EXPORT_CONTROLLED: 3,
}


class DualUseMarker(str, Enum):
    """
    Dual-use category marker for a value.

    The precedence order used by `propagate(...)` is
    NONE < CYBER < CHEM < BIO < NUCLEAR, chosen so that a value
    derived from inputs with mixed markers picks up the
    most-restrictive marker. Where downstream code needs to know
    *all* markers present in the input chain (rather than only the
    most-restrictive one), it should inspect `provenance_chain` and
    walk back to the source tags via the registry; see the
    module-level docstring for the planned `frozenset` enhancement.
    """

    NONE = "none"
    CYBER = "cyber"
    CHEM = "chem"
    BIO = "bio"
    NUCLEAR = "nuclear"

    @property
    def _rank(self) -> int:
        return _DUAL_USE_RANK[self]


_DUAL_USE_RANK = {
    DualUseMarker.NONE: 0,
    DualUseMarker.CYBER: 1,
    DualUseMarker.CHEM: 2,
    DualUseMarker.BIO: 3,
    DualUseMarker.NUCLEAR: 4,
}


class TrustTier(str, Enum):
    """
    Per-session trust tier driven by `TrustScorer`. Not stored on
    `CapabilityTag` -- the tag describes the value, not the session
    in which it was produced. Lives here because the three enums are
    used together everywhere VISTAGuard makes policy decisions.

    Order: NORMAL > ELEVATED > RESTRICTED > TERMINATED, in the sense
    that NORMAL has *more* permissions; the rank reflects
    permission level, not numerical magnitude.
    """

    NORMAL = "normal"
    ELEVATED = "elevated"
    RESTRICTED = "restricted"
    TERMINATED = "terminated"


@dataclass(frozen=True)
class CapabilityTag:
    """
    Provenance and trust tags carried with every value crossing a
    boundary.

    Frozen: derived tags are produced by `CapabilityRegistry.propagate`
    or by calling `tag.with_changes(...)`. Direct field assignment is
    not supported because tag immutability is the structural property
    that prevents a compromised Q-LLM from silently lowering trust on
    a value.

    Fields:
        source: free-form identifier of where this value came from.
            Conventional prefixes: `user:<user_id>`, `tool:<tool_name>`,
            `rag:<corpus>`, `agent:<agent_id>`, `instrument:<id>`,
            `derived` (for values produced by propagation).
        sensitivity: DOE sensitivity tier. Defaults OPEN.
        dual_use: dual-use marker. Defaults NONE.
        taint: default-deny taint flag. True means "untrusted until
            cleared by a gate." Defaults True.
        provenance_chain: ordered list of source identifiers and
            sink operations that produced this value. Append-only by
            convention (replace with a longer list in derived tags).
        metadata: free-form dict for gate-specific annotations.
            Examples: `{"chunk_hash": "<sha256>"}` from G3,
            `{"intent_summary": "..."}` from G1 Q-LLM extraction.
    """

    source: str
    sensitivity: SensitivityTier = SensitivityTier.OPEN
    dual_use: DualUseMarker = DualUseMarker.NONE
    taint: bool = True
    provenance_chain: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_changes(self, **changes: Any) -> "CapabilityTag":
        """
        Return a new `CapabilityTag` with the specified fields
        replaced. Useful when a gate clears taint or adds metadata
        without going through `propagate`. The original tag is
        unmodified.
        """
        return replace(self, **changes)


class CapabilityRegistry:
    """
    Per-session map of value-id -> `CapabilityTag`.

    A `CapabilityRegistry` is held on `ProjectAgent` (in the
    `VistaGuardSidecar` instance) and is the only place tags are
    persisted within a session. The registry is in-memory and
    session-scoped; durable provenance is the responsibility of
    `ProvenanceEmitter`.

    The registry is not thread-safe. VISTA's agent loop runs each
    `ProjectAgent.run_stream` invocation in a single async task and
    does not share `ProjectAgent` instances across concurrent
    requests, so single-task access is sufficient. If a future
    deployment needs to share a registry across tasks, wrap accesses
    in an `asyncio.Lock` at the call sites; we deliberately do not
    add a lock here because the no-contention path is the common
    one.
    """

    def __init__(self) -> None:
        self._tags: dict[str, CapabilityTag] = {}

    # -----------------------------------------------------------------
    # Basic registry operations
    # -----------------------------------------------------------------

    def tag(self, value_id: str, tag: CapabilityTag) -> None:
        """
        Record `tag` as the capability of `value_id`.

        Overwrites any previous tag for the same `value_id`. Callers
        that want append-style provenance for the same value should
        choose distinct `value_id`s per write (e.g., suffix with a
        version) or use `propagate` to derive a new tag.
        """
        self._tags[value_id] = tag

    def get(self, value_id: str) -> CapabilityTag | None:
        """Return the tag for `value_id`, or None if not present."""
        return self._tags.get(value_id)

    def __contains__(self, value_id: str) -> bool:
        return value_id in self._tags

    def __len__(self) -> int:
        return len(self._tags)

    # -----------------------------------------------------------------
    # Composition
    # -----------------------------------------------------------------

    def propagate(
        self,
        source_ids: list[str],
        target_id: str,
        sink: str,
    ) -> CapabilityTag:
        """
        Compute a derived `CapabilityTag` for `target_id` from the
        registered tags of `source_ids`, record it, and return it.

        The derived tag carries the lattice join of the inputs:

        - `sensitivity` = max of input sensitivities along
          OPEN < INTERNAL < CUI < EXPORT_CONTROLLED.
        - `taint` = OR of input taints.
        - `dual_use` = max of input dual-use markers along
          NONE < CYBER < CHEM < BIO < NUCLEAR.
        - `provenance_chain` = concatenation of input chains,
          appended with `f"{sink}<-({source_ids})"`.
        - `metadata` = shallow merge of input metadata.

        `sink` identifies the operation that combined the sources
        (e.g., `"tool:submit_hpc_job"`, `"rag:rag_search"`,
        `"agent:plan_step"`). It is recorded in the provenance chain
        so post-hoc audit can reconstruct *why* a derived value
        exists.

        Raises `KeyError` if any `source_id` is not in the registry.
        This is the safe failure mode: rather than silently producing
        a derived tag with default (OPEN, NONE, untainted)
        capabilities, we refuse to compute and force the caller to
        explain. `target_id` is *not* allowed to collide with an
        existing tag unless the caller has already deleted that
        entry; see `__setitem__`-style overwrite via `tag(...)`.

        With zero `source_ids`, the derived tag has the default
        (OPEN, NONE, untainted) capabilities and a provenance chain
        of `[f"{sink}<-()"]`. Callers should generally not propagate
        from no sources; for the no-source case, prefer `tag(...)`
        directly.
        """
        if not source_ids:
            derived = CapabilityTag(
                source="derived",
                sensitivity=SensitivityTier.OPEN,
                dual_use=DualUseMarker.NONE,
                taint=False,
                provenance_chain=(f"{sink}<-()",),
                metadata={},
            )
            self._tags[target_id] = derived
            return derived

        missing = [sid for sid in source_ids if sid not in self._tags]
        if missing:
            raise KeyError(
                f"Cannot propagate to {target_id!r}: unknown source(s) "
                f"{missing!r}. Register source tags before propagating."
            )

        source_tags = [self._tags[sid] for sid in source_ids]

        # Join: maximum along the rank order, OR of taints.
        joined_sensitivity = max(
            (t.sensitivity for t in source_tags),
            key=lambda s: s._rank,
        )
        joined_dual_use = max(
            (t.dual_use for t in source_tags),
            key=lambda d: d._rank,
        )
        joined_taint = any(t.taint for t in source_tags)

        # Provenance: concatenate all input chains, then record the
        # composition step.
        provenance: list[str] = []
        for t in source_tags:
            provenance.extend(t.provenance_chain)
        provenance.append(f"{sink}<-({','.join(source_ids)})")

        # Metadata: shallow merge. Later sources win on collisions;
        # this matches Python's `dict | dict` ordering. Metadata is
        # informational, not enforcement-bearing, so the merge
        # policy can be simple.
        merged_metadata: dict[str, Any] = {}
        for t in source_tags:
            merged_metadata.update(t.metadata)

        derived = CapabilityTag(
            source="derived",
            sensitivity=joined_sensitivity,
            dual_use=joined_dual_use,
            taint=joined_taint,
            provenance_chain=tuple(provenance),
            metadata=merged_metadata,
        )
        self._tags[target_id] = derived
        return derived

    # -----------------------------------------------------------------
    # Querying
    # -----------------------------------------------------------------

    def find(
        self,
        *,
        source_pattern: str | None = None,
        sensitivity: SensitivityTier | None = None,
        dual_use: DualUseMarker | None = None,
        tainted: bool | None = None,
    ) -> Iterator[tuple[str, CapabilityTag]]:
        """
        Yield `(value_id, tag)` pairs matching all of the given
        filters. Any filter set to None is ignored.

        `source_pattern` uses `fnmatch`-style globbing, so callers
        can write `"tool:*"` to match any tool source,
        `"rag:internal-*"` to match internal RAG corpora, or
        `"tool:submit_hpc_job"` for an exact match. The other
        filters require exact equality.

        The iterator is lazy and safe to abandon mid-iteration. The
        registry is not modified during iteration; if a caller needs
        to delete or re-tag values it found, it should materialize
        the iterator to a list first.
        """
        for value_id, tag in self._tags.items():
            if source_pattern is not None and not fnmatch(tag.source, source_pattern):
                continue
            if sensitivity is not None and tag.sensitivity is not sensitivity:
                continue
            if dual_use is not None and tag.dual_use is not dual_use:
                continue
            if tainted is not None and tag.taint is not tainted:
                continue
            yield value_id, tag