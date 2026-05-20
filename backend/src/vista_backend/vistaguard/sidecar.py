"""
VistaGuardSidecar -- the per-session composite that `ProjectAgent`
instantiates.

The sidecar owns the four Phase-0 collaborators -- capability
registry, trust scorer, incident manager, provenance emitter -- and
exposes the hook surface that `ProjectAgent` composes into its
PydanticAI tool-call chain. From the integration plan §1:

> Quarantine logic is concentrated in
> `backend/src/vista_backend/agents/agents.py:ProjectAgent` so it
> composes once and is reused everywhere `ProjectAgent` is.

In Phase 0 the sidecar is a *shell*: gates are not yet built (the
`_build_gates()` factory returns an empty dict), so `is_active()` is
False even with the master flag on, and `process_tool_call` is a
strict pass-through. The shell exists so the next phase-0 issue
(`Integrate VistaGuardSidecar into ProjectAgent`) can wire it into
`agents.py` without behavioral change -- gates land in Phase 1+ and
flip `is_active()` to True as their flags are enabled.

## API contract

- `VistaGuardSidecar(settings, project)` constructs without error
  for any settings (including `enabled=False`). The Q-LLM is not
  instantiated here; Phase 1 will call `attach_quarantine_agent` on
  the constructed sidecar after the Agent is built.

- `is_active()` returns True iff the master flag is on AND at least
  one gate is in the active set. Phase-0 always returns False
  because `_build_gates()` returns an empty dict.

- `is_gate_enabled(name)` returns True iff the named gate (G1..G7)
  is in the active set. Phase-0 always returns False for the same
  reason. The argument is the short gate identifier (e.g., ``"G2"``)
  matching `Gate.name`.

- `process_tool_call(ctx, call_tool, tool_name, args)` is the
  PydanticAI `ProcessToolCallback` surface. In Phase 0 it is a
  strict pass-through: `return await call_tool(tool_name, args)`.
  Phases 1+ replace the body with the fast/slow gate chain.

- `attach_quarantine_agent(agent)` stores a PydanticAI Agent (the
  Q-LLM) on the sidecar for gates to invoke via
  `ctx.quarantine_agent`. Phase-0 just records it; Phase 1's G2
  reads it.

## What's *not* in Phase 0

- Gate instantiation. `_build_gates()` is a stub that returns `{}`.
- Contract library. The `contracts` attribute is None; Phase 5
  populates it.
- Hook composition with the kb-scope callback in `agents.py`. That
  is the next phase-0 issue (`Integrate VistaGuardSidecar into
  ProjectAgent`).

The sidecar deliberately holds *references* to its collaborators
rather than copies, so Phase-5 mutations to the trust scorer or
contract library are visible to gates without a re-wire.
"""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ..db.schemas import ProjectPublic
from .capabilities import CapabilityRegistry
from .config import VistaGuardSettings
from .gates.base import Gate
from .incidents import IncidentManager
from .provenance import ProvenanceEmitter
from .trust import TrustScorer


# -----------------------------------------------------------------
# Type aliases
# -----------------------------------------------------------------
#
# `CallToolFn` matches the inner callback the PydanticAI MCP layer
# hands to a `ProcessToolCallback`. We re-declare it here rather
# than importing the full `ProcessToolCallback` type to keep the
# sidecar testable without spinning up a real PydanticAI run
# context. The signature -- (name, args, metadata=None) -> result
# -- is the contract callers rely on; the result type is left as
# Any because PydanticAI returns a union of every supported tool
# return shape and pinning it here would only get in the way of
# tests using fake callables.

CallToolFn = Callable[..., Awaitable[Any]]


# -----------------------------------------------------------------
# VistaGuardSidecar
# -----------------------------------------------------------------


class VistaGuardSidecar:
    """
    Per-`ProjectAgent` composite that owns the VISTAGuard runtime
    state.

    One sidecar is constructed per `ProjectAgent.run_stream`
    invocation (i.e., per agent turn from VISTA's perspective). The
    in-memory state -- capability registry, trust scorer, incident
    manager's `last_incident`, provenance emitter's `last_event` --
    is therefore session-scoped; durable audit lives in the
    provenance JSONL (or, post-Phase 7, the Flowcept broker).

    Phase-0 instances are inert: `is_active()` is False, the gate
    set is empty, and `process_tool_call` passes through. The
    structural acceptance criterion ("with `enabled=true` but all
    gates disabled, `process_tool_call` is byte-identical to direct
    `call_tool` invocation") is enforced by the construction order:
    `is_active()` consults `self._gates`, which `_build_gates()`
    leaves empty in Phase 0, so the disabled branch in
    `process_tool_call` runs unconditionally.

    Not thread-safe by design (see `CapabilityRegistry`'s docstring
    for the rationale).
    """

    def __init__(
        self,
        settings: VistaGuardSettings,
        project: ProjectPublic,
    ) -> None:
        """
        Construct a sidecar for the given project.

        Args:
            settings: the VISTAGuard sub-model. Read for the
                master/per-gate toggles, flowcept config, and
                provenance log path. No env reads happen here --
                the parent `Settings` class is responsible for
                populating the sub-model.
            project: the `ProjectPublic` whose agent turn this
                sidecar is auditing. Stored for Phase-5 gates that
                consult the project's knowledge-base list, tool
                allow-list, or sensitivity settings. Phase-0 code
                does not yet read project fields off the sidecar.

        Raises:
            ValueError: if `settings.flowcept_enabled is True` and
                `flowcept_endpoint is None`. The exception
                originates in `ProvenanceEmitter` and is allowed to
                propagate -- the integration-plan §5 contract is
                that mis-configured Flowcept settings fail at boot,
                and the sidecar constructor is the earliest point
                that catches it.
        """
        self._settings = settings
        self._project = project

        # Build collaborators in dependency order so the constructor
        # can wire IncidentManager to the live trust scorer and
        # provenance emitter rather than retrofitting them later.
        self._capability_registry = CapabilityRegistry()
        self._trust_scorer = TrustScorer(settings)
        self._provenance = ProvenanceEmitter(settings)
        self._incidents = IncidentManager(
            settings,
            trust_scorer=self._trust_scorer,
            provenance=self._provenance,
        )

        # Phase-1 will assign this via `attach_quarantine_agent`.
        self._quarantine_agent: Any | None = None

        # Phase-5 will populate from `settings.contracts_dir`.
        self._contracts: Any | None = None

        # `_build_gates()` is the single point where gate
        # construction lives. Phase 0 returns {}; Phase 1+ will
        # populate this dict conditioned on the per-gate flags.
        self._gates: dict[str, Gate] = self._build_gates()

    # -----------------------------------------------------------------
    # Collaborators (read-only properties)
    # -----------------------------------------------------------------

    @property
    def settings(self) -> VistaGuardSettings:
        """The VISTAGuard settings sub-model this sidecar was built with."""
        return self._settings

    @property
    def project(self) -> ProjectPublic:
        """The project whose agent turn this sidecar audits."""
        return self._project

    @property
    def capability_registry(self) -> CapabilityRegistry:
        return self._capability_registry

    @property
    def trust_scorer(self) -> TrustScorer:
        return self._trust_scorer

    @property
    def incident_manager(self) -> IncidentManager:
        return self._incidents

    @property
    def provenance(self) -> ProvenanceEmitter:
        return self._provenance

    @property
    def quarantine_agent(self) -> Any | None:
        """The Q-LLM agent attached via `attach_quarantine_agent`, or None."""
        return self._quarantine_agent

    @property
    def contracts(self) -> Any | None:
        """The Phase-5 contract library, or None until Phase 5 wires it."""
        return self._contracts

    @property
    def gates(self) -> Mapping[str, Gate]:
        """
        Read-only view of the active gate set.

        Phase 0 is empty. Returned as a `Mapping` so callers can't
        mutate the dict in-place; gate insertion is the sidecar's
        responsibility via `_build_gates()`.
        """
        return self._gates

    # -----------------------------------------------------------------
    # Active-state predicates
    # -----------------------------------------------------------------

    def is_active(self) -> bool:
        """
        True iff the master flag is on AND at least one gate is in
        the active set.

        - `enabled=False` -> False (master flag off).
        - `enabled=True`, no gates built (Phase 0) -> False.
        - `enabled=True`, at least one gate built (Phase 1+) -> True.

        The "no gates" branch is the load-bearing piece: a
        deployment that flips the master flag on without enabling
        any individual gate sees no behavioral change, because
        `is_active()` is the predicate `ProjectAgent` will consult
        before running any sidecar logic.
        """
        return self._settings.enabled and bool(self._gates)

    def is_gate_enabled(self, name: str) -> bool:
        """
        True iff the named gate is in the active set.

        `name` is the short gate identifier (``"G1"`` .. ``"G7"``)
        matching `Gate.name`. Returns False for unknown names
        rather than raising, matching the contract that callers
        (in particular Phase-3's `agents.py` G1 early-rejection
        path) can ask "is G1 active?" unconditionally without
        first checking whether the sidecar even has a G1
        implementation.

        Phase 0 returns False for every name because `_build_gates()`
        produces an empty dict. The acceptance criterion explicitly
        names G1-G7; we don't special-case those names because
        absence from `self._gates` is already the right answer.
        """
        return name in self._gates

    # -----------------------------------------------------------------
    # Q-LLM attachment
    # -----------------------------------------------------------------

    def attach_quarantine_agent(self, agent: Any) -> None:
        """
        Store the PydanticAI Q-LLM Agent for gates to invoke.

        Phase 0 just records the reference; gates read it via
        `GateContext.quarantine_agent` (the context is constructed
        per-call by `process_tool_call` in Phase 1+).

        Calling this twice replaces the previous agent. There is
        no Phase-0 use case for multiple Q-LLMs and the simple
        replace semantics make the Phase-1 wiring straightforward.

        Typed `Any` because pinning the type to
        `pydantic_ai.Agent` here would force every test and import
        site to drag in PydanticAI even for the disabled-flag
        path. Phase-1 will tighten this when the first real caller
        passes a real `Agent`.
        """
        self._quarantine_agent = agent

    # -----------------------------------------------------------------
    # PydanticAI ProcessToolCallback surface
    # -----------------------------------------------------------------

    async def process_tool_call(
        self,
        ctx: Any,
        call_tool: CallToolFn,
        tool_name: str,
        args: dict[str, Any],
    ) -> Any:
        """
        The PydanticAI tool-call hook.

        In Phase 0 this is a strict pass-through: it forwards
        directly to `call_tool(tool_name, args)`. The implementation
        is *unconditional* (no `if self.is_active()` guard) because
        Phase 0 does not yet have any gate logic to gate on -- the
        guard arrives in Phase 1 alongside the first real gate body.

        The signature matches PydanticAI's `ProcessToolCallback`
        outer hook: `(ctx, call_tool, tool_name, args) -> result`.
        The inner `call_tool` accepts a third `metadata` argument
        that PydanticAI uses internally; the Phase-0 pass-through
        omits it and relies on PydanticAI's default. Phase-1 gates
        that need to forward metadata can extend the call site.

        Returns:
            The result of `call_tool(tool_name, args)` unmodified.
        """
        return await call_tool(tool_name, args)

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    def _build_gates(self) -> dict[str, Gate]:
        """
        Construct the active gate set from the per-gate flags.

        Phase 0 returns `{}` because no concrete gate class exists
        yet (`PassThroughGate` is a base-class test fixture, not a
        production gate). Phase 1+ will populate this conditioned
        on the `g{N}_enabled` flags:

            gates: dict[str, Gate] = {}
            if self._settings.g1_enabled:
                gates["G1"] = PromptGate(...)
            if self._settings.g2_enabled:
                gates["G2"] = ToolGate(...)
            ...
            return gates

        Keeping this as a separate method (rather than inlining in
        `__init__`) means Phase 1+ can override the gate-build
        policy in tests by subclassing `VistaGuardSidecar`. We do
        not expect to use that hook in production -- the flag-based
        construction is the supported configuration mechanism.
        """
        return {}
