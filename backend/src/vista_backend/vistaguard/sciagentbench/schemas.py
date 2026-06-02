"""
Frozen instance / trace / scorer schemas for SciAgentBench.

These dataclasses are the *format contract* the work item calls for
"frozen at end of week 25 so Phase 10 authoring doesn't churn the
format." Instances live on disk as YAML (or JSON); this module is the
in-memory shape the loader produces and the runner / scorer consume.

The canonical JSON-Schema serialization of the *instance* format lives
next to this module at ``schemas/instance.schema.json`` and is what an
external collaborator (Phase 12) validates their authored instances
against. ``instance_loader.validate_instance_dict`` checks loaded
documents against it. The trace and scorer schemas are Python-internal
(produced, never authored by hand) so they are pinned here in code
rather than as authoring schemas.

## The execution model

A SciAgentBench *instance* models one attack (or one benign task) as a
sequence of **sessions**, each a sequence of **turns**, each a sequence
of **actions**. An action is the atomic unit the harness routes through
a gate:

- ``PROMPT``   -- a user/agent prompt. Defended by **G1**.
- ``TOOL_CALL``-- a tool invocation (``run_bash``, ``create_file``,
  ``submit_hpc_job``, ...). Defended by **G2** (generic), **G4**
  (code), or **G5** (HPC) depending on the tool.
- ``MEMORY_WRITE`` -- the agent writes a value into the in-process turn
  buffer (the cross-session substrate for one episode). The *plant* half
  of a multi-turn sequence.
- ``MEMORY_READ``  -- the agent retrieves from the turn buffer. Defended
  by **G3**. The *retrieve-and-act* half. (Durable cross-session memory,
  and the MINJA/MemoryGraft sequences it enables, are deferred to
  CHUNKS -- see ``sciagentbench/deferred/``.)
- ``RAG_RETRIEVE`` -- a retrieval against a corpus. Defended by **G3**.
- ``RESPONSE``     -- final assistant text. Not gated; the scorer reads
  it for utility / leak checks.

Authoring an instance is "what would the compromised agent *attempt*":
the harness supplies the real gates, the ablation matrix toggles which
gates are live, and the scorer reads whether the attack-flagged actions
got through. This mirrors the existing per-gate eval runners
(``g1_runner`` et al.), generalized to multi-session episodes with a
shared memory substrate.

Every dataclass here is ``frozen=True`` so a loaded instance can't be
mutated mid-run -- the same immutability discipline the rest of
VISTAGuard uses for capability tags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# -----------------------------------------------------------------
# Action kinds and the boundary -> gate routing
# -----------------------------------------------------------------


class ActionKind(str, Enum):
    """The atomic units the harness routes through gates."""

    PROMPT = "prompt"
    TOOL_CALL = "tool_call"
    MEMORY_WRITE = "memory_write"
    MEMORY_READ = "memory_read"
    RAG_RETRIEVE = "rag_retrieve"
    RESPONSE = "response"


#: Which gate id defends which action kind *by default* when an action
#: does not name its own defender. ``TOOL_CALL`` is intentionally absent
#: -- a tool call is routed by tool name (``submit_hpc_job`` -> G5,
#: ``run_bash`` / ``create_file`` -> G4, everything else -> G2), which
#: the runner resolves; authors can always override with ``Action.gate``.
DEFAULT_GATE_FOR_KIND: dict[ActionKind, str | None] = {
    ActionKind.PROMPT: "G1",
    ActionKind.TOOL_CALL: None,
    ActionKind.MEMORY_WRITE: None,
    ActionKind.MEMORY_READ: "G3",
    ActionKind.RAG_RETRIEVE: "G3",
    ActionKind.RESPONSE: None,
}


# -----------------------------------------------------------------
# Capability-tag spec (provenance the runner registers pre-gate)
# -----------------------------------------------------------------


@dataclass(frozen=True)
class CapabilitySpec:
    """
    Declarative capability tag an action attaches to a value before
    the gate runs.

    The runner registers this into the per-session ``CapabilityRegistry``
    so the gate (and the scorer's hard-win check) can reason about
    provenance. The fields mirror ``capabilities.CapabilityTag``.

    The v0.2 capability model is ``(source, taint, dual_use)`` -- the
    sensitivity / CUI tier was dropped (deferred with the controlled-data
    scope). A ``MEMORY_WRITE`` action's ``CapabilitySpec`` rides along
    into the turn buffer so that a later ``MEMORY_READ`` re-registers the
    same tag -- this is how a planted ``taint=True`` value keeps its tag
    across the sessions of an episode.
    """

    value_id: str
    source: str = "user:operator"
    dual_use: str = "none"
    taint: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "value_id": self.value_id,
            "source": self.source,
            "dual_use": self.dual_use,
            "taint": self.taint,
        }


# -----------------------------------------------------------------
# Action / Turn / Session
# -----------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    """
    One atomic step the (possibly-compromised) agent attempts.

    Fields:
        kind: see ``ActionKind``.
        payload: the gate-ready payload for this action's gate.
            Conventionally the exact dict the gate's ``check_fast``
            expects -- e.g. ``{"user_prompt": ...}`` for G1,
            ``{"tool_name": ..., "args": ...}`` for G4,
            ``{"slurm_script": ..., "user_config": {...}}`` for G5.
            For ``MEMORY_WRITE`` / ``MEMORY_READ`` it carries
            ``{"key": ..., "content": ...}``.
        gate: explicit defender gate id ("G1".."G5"). When None the
            runner resolves it from ``kind`` (and tool name for
            ``TOOL_CALL``).
        is_attack: True if this action is part of the adversarial
            objective. The scorer's ASR numerator counts attack
            actions that were *allowed* (not blocked by a live gate).
        is_utility: True if this action represents the legitimate
            scientific task completing. Utility metrics (BU / UA) count
            these.
        label: short human label, surfaced in the trace and report.
        capability: optional provenance tag registered before the gate
            runs (and carried across sessions on a MEMORY_WRITE).
    """

    kind: ActionKind
    payload: dict[str, Any] = field(default_factory=dict)
    gate: str | None = None
    is_attack: bool = False
    is_utility: bool = False
    label: str = ""
    capability: CapabilitySpec | None = None

    def resolved_gate(self) -> str | None:
        """The gate id that defends this action.

        Explicit ``gate`` wins; otherwise fall back to the per-kind
        default, with tool-name routing for ``TOOL_CALL``.
        """
        if self.gate is not None:
            return self.gate
        if self.kind is ActionKind.TOOL_CALL:
            tool = str(self.payload.get("tool_name", ""))
            if tool == "submit_hpc_job":
                return "G5"
            if tool in ("run_bash", "create_file"):
                return "G4"
            return "G2"
        return DEFAULT_GATE_FOR_KIND.get(self.kind)


@dataclass(frozen=True)
class Turn:
    """One agent turn: an ordered list of actions.

    A turn typically opens with a ``PROMPT`` action (the user message)
    followed by whatever tool calls / memory ops the agent attempts in
    response.
    """

    actions: tuple[Action, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class Session:
    """One session in a (possibly multi-session) episode.

    ``session_id`` is the deterministic id the ``SessionRunner`` threads
    through the in-process turn buffer. For single-session instances there
    is one ``Session``; multi-session instances (e.g. a session-A write +
    session-B read within one episode) have several.
    """

    session_id: str
    turns: tuple[Turn, ...] = ()


# -----------------------------------------------------------------
# Success criteria (programmatic + LLM-judge fallback)
# -----------------------------------------------------------------


@dataclass(frozen=True)
class SuccessCriterion:
    """
    How the scorer decides whether an objective was met.

    Fields:
        check: key of a registered programmatic checker in
            ``scorer.PROGRAMMATIC_CHECKS``. The checker reads the
            ``Trace`` and returns ``True`` / ``False`` /
            ``None`` (indeterminate -> fall through to the judge).
        params: keyword arguments forwarded to the checker.
        judge_prompt: the prompt the LLM-judge fallback uses when the
            programmatic checker returns ``None``. Empty disables the
            fallback (indeterminate then scores as a miss, recorded).
    """

    check: str
    params: dict[str, Any] = field(default_factory=dict)
    judge_prompt: str = ""


# -----------------------------------------------------------------
# Instance (the authored unit)
# -----------------------------------------------------------------


@dataclass(frozen=True)
class Instance:
    """
    A single SciAgentBench instance.

    Fields:
        instance_id: stable unique id (e.g. ``"b1_1_format_hijack_01"``).
        boundary: boundary label, e.g. ``"B1.1"``.
        template: template key the instance belongs to.
        kind: ``"attack"`` or ``"benign"``. Benign instances feed BU;
            attack instances feed UA + ASR.
        memory_namespace: the shared key cross-session memory is scoped
            under. All sessions of one instance share it; distinct
            instances get distinct namespaces so runs don't bleed.
        sessions: the ordered sessions making up the episode.
        success_criterion: attack-success (or benign-task-success)
            predicate.
        utility_criterion: optional legitimate-task-completion predicate
            used for UA on attack instances. None -> utility not scored
            for this instance.
        description / references / variation_axis: documentation
            metadata, surfaced by the Phase-12 spec + playbook.
    """

    instance_id: str
    boundary: str
    template: str
    kind: str
    sessions: tuple[Session, ...]
    success_criterion: SuccessCriterion
    memory_namespace: str = ""
    utility_criterion: SuccessCriterion | None = None
    description: str = ""
    references: tuple[str, ...] = ()
    variation_axis: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ("attack", "benign"):
            raise ValueError(
                f"Instance {self.instance_id!r}: kind must be "
                f"'attack' or 'benign', got {self.kind!r}"
            )
        if not self.sessions:
            raise ValueError(
                f"Instance {self.instance_id!r}: at least one session required"
            )

    @property
    def effective_namespace(self) -> str:
        """Namespace to scope memory under -- defaults to the id."""
        return self.memory_namespace or self.instance_id

    @property
    def is_attack(self) -> bool:
        return self.kind == "attack"
