"""
Trace recorder for the SciAgentBench harness.

A ``Trace`` is the structured record of one instance run under one
ablation configuration: every action the agent attempted, the gate
decision that met it, and a snapshot of the capability registry at the
end. It is the single artifact the scorer reads, and (Phase 13) the RL
reward's discriminator reads -- so the recorder is deliberately the only
place execution facts are written down.

The recorder is gate-agnostic: the ``SessionRunner`` hands it
``ActionRecord``s as it produces them. Nothing here imports a gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# -----------------------------------------------------------------
# Records
# -----------------------------------------------------------------


@dataclass(frozen=True)
class ActionRecord:
    """The recorded outcome of one action passing (or not) a gate.

    Fields:
        session_id: which session this action ran in.
        turn_index: 0-based turn within the session.
        action_index: 0-based action within the turn.
        kind: the ``ActionKind`` value (string).
        gate: the gate id that defended the action, or None if no gate
            defends this action kind (or the defending gate was off in
            this config -- see ``defender_live``).
        defender_live: True if the defending gate was enabled in this
            config and actually ran. False means the action passed
            because no live gate was watching.
        is_attack / is_utility: copied from the action.
        label: copied from the action.
        allowed: the action's final disposition -- True if it was *not*
            blocked. Attack ASR counts allowed attack actions.
        blocked_by: gate id that denied the action, else None.
        reason: the gate's decision reason (or a pass-through note).
        incident_level: the gate's incident level, if any.
        value_id: the capability value id this action registered, if any.
        capability: snapshot of the registered tag fields, if any.
    """

    session_id: str
    turn_index: int
    action_index: int
    kind: str
    gate: str | None
    defender_live: bool
    is_attack: bool
    is_utility: bool
    label: str
    allowed: bool
    blocked_by: str | None
    reason: str
    incident_level: int | None = None
    value_id: str | None = None
    capability: dict[str, Any] | None = None


@dataclass(frozen=True)
class Trace:
    """Full record of one instance run under one configuration.

    Fields:
        instance_id / boundary / template: copied from the instance.
        config_name: the ablation configuration name.
        kind: ``"attack"`` or ``"benign"``.
        actions: every ``ActionRecord``, in execution order.
        final_tags: ``(value_id, tag_fields)`` snapshot of every
            session's capability registry at the end of the run. Used by
            the scorer's hard-win check (a tainted value reaching a
            high-privilege sink).
        terminated: True if a session hit a SEV1 termination during the
            run.
    """

    instance_id: str
    boundary: str
    template: str
    config_name: str
    kind: str
    actions: tuple[ActionRecord, ...] = ()
    final_tags: tuple[tuple[str, dict[str, Any]], ...] = ()
    terminated: bool = False

    # -------------------------------------------------------------
    # Convenience views for the scorer
    # -------------------------------------------------------------

    def attack_actions(self) -> list[ActionRecord]:
        return [a for a in self.actions if a.is_attack]

    def utility_actions(self) -> list[ActionRecord]:
        return [a for a in self.actions if a.is_utility]

    def allowed_attack_actions(self) -> list[ActionRecord]:
        return [a for a in self.actions if a.is_attack and a.allowed]


# -----------------------------------------------------------------
# Recorder
# -----------------------------------------------------------------


class TraceRecorder:
    """Accumulates ``ActionRecord``s and finalizes a ``Trace``.

    The ``SessionRunner`` constructs one recorder per (instance, config)
    run, calls ``record_action`` per action, ``snapshot_tags`` once per
    session as it ends, and ``finalize`` at the end.
    """

    def __init__(
        self,
        *,
        instance_id: str,
        boundary: str,
        template: str,
        config_name: str,
        kind: str,
    ) -> None:
        self._instance_id = instance_id
        self._boundary = boundary
        self._template = template
        self._config_name = config_name
        self._kind = kind
        self._actions: list[ActionRecord] = []
        self._tags: list[tuple[str, dict[str, Any]]] = []
        self._terminated = False

    def record_action(self, record: ActionRecord) -> None:
        self._actions.append(record)

    def snapshot_tags(self, tags: list[tuple[str, dict[str, Any]]]) -> None:
        """Append a session's end-of-run capability tags to the trace."""
        self._tags.extend(tags)

    def mark_terminated(self) -> None:
        self._terminated = True

    def finalize(self) -> Trace:
        return Trace(
            instance_id=self._instance_id,
            boundary=self._boundary,
            template=self._template,
            config_name=self._config_name,
            kind=self._kind,
            actions=tuple(self._actions),
            final_tags=tuple(self._tags),
            terminated=self._terminated,
        )
