"""
`VistaGuardCapability` -- the PydanticAI-capability base class every
VISTAGuard gate adapter subclasses.

Phase 3.5 migrates VISTAGuard off the bespoke `process_tool_call`
composition and onto PydanticAI's `AbstractCapability` + Hooks API.
Under the new shape, each gate (G1..G7) is exposed to the agent as an
`AbstractCapability` subclass that registers the relevant hooks
(`before_run`, `before_tool_execute`, `after_tool_execute`, ...) and
delegates the actual policy decision to the gate's existing
`check_fast` / `check_slow` logic.

This module (issue R0) lands only the shared base class -- no gate
hooks yet. R1..R5 populate the per-gate subclasses
(`G1PromptCapability`, `G2ToolCapability`, ...). Because R0 is purely
additive, nothing here is wired into `agents.py` yet; the existing
sidecar `process_tool_call` path keeps running unchanged.

## Responsibilities of the base class

- Hold a reference to the owning `VistaGuardSidecar` so subclass hooks
  can reach the per-session capability registry, trust scorer, and
  (Phase 1+) Q-LLM agents. The sidecar is threaded into hooks via
  `RunContext.deps`; the reference stored here is the construction-time
  handle the subclass uses to build that context.
- Hold the underlying `Gate` instance. The gate keeps its own
  `check_fast` / `extract_intent` / `sanitize_chunks` methods, which
  the existing per-gate unit tests exercise directly. Keeping the gate
  as a delegate (rather than folding its logic into the capability)
  means those tests continue to pass untouched through the migration.
- Provide `is_enabled()`, the single place the master flag and the
  per-gate flag are AND-ed together. A disabled capability is a no-op:
  subclass hooks check `is_enabled()` first and return early when off.

## Why a custom ``__init__`` rather than dataclass fields

`AbstractCapability` is itself a (field-less) dataclass, but VISTAGuard
capabilities carry live collaborator references (a sidecar, a gate)
rather than serializable config, so a plain constructor is clearer than
dataclass fields. PydanticAI's `for_run` default returns ``self`` and
the framework never reconstructs a capability via `dataclasses.replace`,
so overriding `__init__` is safe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.capabilities import AbstractCapability

if TYPE_CHECKING:
    # Imported for typing only. A runtime import of `sidecar` here would
    # be circular: `sidecar.py` imports `CapabilityRegistry` from this
    # package, which imports this module. The collaborators are only
    # ever *stored* at runtime, never constructed, so type-only imports
    # are sufficient.
    from ..config import VistaGuardSettings
    from ..gates.base import Gate
    from ..sidecar import VistaGuardSidecar


class VistaGuardCapability(AbstractCapability):
    """
    Base class for every gate's PydanticAI-capability adapter.

    Subclasses register hooks (via the `@hooks.on.*` decorators or by
    overriding the `before_*` / `after_*` methods) and delegate policy
    to `self.gate`. They should guard every hook body with
    `if not self.is_enabled(): return ...` so that a disabled gate is a
    structural no-op -- the same modularity contract the `Gate` base
    class enforces for `check_fast`.

    Construction:
        VistaGuardCapability(sidecar, gate, settings)

    The argument order matches the R0 acceptance criterion. The three
    collaborators are exposed as read-only properties for subclass
    hooks and unit tests.
    """

    #: The `VistaGuardSettings` field name carrying this gate's enable
    gate_flag: str | None = None

    def __init__(
        self,
        sidecar: VistaGuardSidecar,
        gate: Gate,
        settings: VistaGuardSettings,
    ) -> None:
        self._sidecar = sidecar
        self._gate = gate
        self._settings = settings

    # -----------------------------------------------------------------
    # Collaborators (read-only properties)
    # -----------------------------------------------------------------

    @property
    def sidecar(self) -> VistaGuardSidecar:
        """The owning sidecar; source of the per-session registry/scorer."""
        return self._sidecar

    @property
    def gate(self) -> Gate:
        """The underlying gate whose `check_*` logic this capability drives."""
        return self._gate

    @property
    def settings(self) -> VistaGuardSettings:
        """The VISTAGuard settings sub-model the enable flags are read from."""
        return self._settings

    # -----------------------------------------------------------------
    # Enablement
    # -----------------------------------------------------------------

    def is_enabled(self) -> bool:
        """
        True iff the master flag AND this gate's per-gate flag are on.

        Returns False when:

        - the master flag (`settings.enabled`) is off, or
        - the per-gate flag (e.g. `settings.g1_enabled`) is off or
          absent.

        A capability for which this returns False must behave as a
        no-op; subclass hooks check this first.
        """
        if not self._settings.enabled:
            return False
        return bool(getattr(self._settings, self._gate_flag_name(), False))

    def _gate_flag_name(self) -> str:
        """
        Resolve the `VistaGuardSettings` field name for this gate's
        enable flag.

        Uses the explicit `gate_flag` class attribute when a subclass
        sets one; otherwise derives it from the gate's `name`
        (``"G2"`` -> ``"g2_enabled"``). The derived name resolves via
        `getattr(..., False)` in `is_enabled`, so a gate whose name has
        no matching settings field is simply treated as disabled.
        """
        if self.gate_flag is not None:
            return self.gate_flag
        return f"{self._gate.name.lower()}_enabled"
