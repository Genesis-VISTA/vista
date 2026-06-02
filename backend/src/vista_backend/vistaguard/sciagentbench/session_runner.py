"""
The multi-session execution harness: ``SessionRunner``.

``ProjectAgent.run_stream`` runs a single turn. SciAgentBench needs to
run a whole *episode*: a deterministic sequence of sessions, each a
sequence of turns, threaded through an **in-process turn buffer** -- so a
value written in session A is visible to a read in session B *within the
same episode* (the multi-turn / crescendo B1 class). ``SessionRunner`` is
that extension. There is no persistent cross-session store; durable
memory (and the MINJA/MemoryGraft sequences) is deferred to CHUNKS.

It is deliberately *not* a live agent driver. Like the existing
per-gate eval runners (``g1_runner`` et al.), it threads authored
actions through the **real** VISTAGuard gate stack offline: the gates
are real, the ablation matrix toggles which are live, the "agent" is the
authored action sequence (what a compromised agent would attempt), and
the scorer reads which attack actions got through. This keeps the whole
harness deterministic and CPU-only while still exercising production
gate code.

For a deployment that wants to drive a *live* ``ProjectAgent`` instead,
the same ``Trace`` contract holds -- a future adapter can produce
``ActionRecord``s from a real run-stream and feed the same scorer. The
authored-action path is what Phase 9's acceptance criterion
(round-tripping one synthetic B1.1 instance) and the Phase 10 corpus
need.
"""

from __future__ import annotations

from typing import Any

from ..capabilities import CapabilityRegistry, CapabilityTag
from ..capabilities.registry import DualUseMarker
from ..config import VistaGuardSettings
from ..gates.base import GateContext, GateDecision
from ..trust import TrustScorer
from .ablation_matrix import AblationConfig, GateStack, build_gate_stack
from .schemas import Action, ActionKind, CapabilitySpec, Instance, Session
from .trace_recorder import ActionRecord, Trace, TraceRecorder
from .turn_buffer import InProcessTurnBuffer, MemoryRecord, MemoryStore


# -----------------------------------------------------------------
# Capability-spec -> CapabilityTag
# -----------------------------------------------------------------
#
# The v0.2 capability model is (source, taint, dual-use) -- no
# sensitivity tier. CapabilityTag still carries a sensitivity field
# (production registry), which defaults to OPEN; the harness never sets
# it.


def _dual_use(value: str) -> DualUseMarker:
    try:
        return DualUseMarker(value)
    except ValueError:
        return DualUseMarker.NONE


def _spec_to_tag(spec: CapabilitySpec) -> CapabilityTag:
    return CapabilityTag(
        source=spec.source,
        dual_use=_dual_use(spec.dual_use),
        taint=spec.taint,
        provenance_chain=(f"sciagentbench:{spec.value_id}",),
    )


def _tag_fields(tag: CapabilityTag) -> dict[str, Any]:
    return {
        "source": tag.source,
        "dual_use": tag.dual_use.value,
        "taint": tag.taint,
    }


# -----------------------------------------------------------------
# SessionRunner
# -----------------------------------------------------------------


class SessionRunner:
    """Runs one ``Instance`` under one ``AblationConfig`` into a ``Trace``.

    Args:
        memory_store: the in-process turn buffer threading values across
            the sessions of one episode. The runner scopes all
            reads/writes under ``instance.effective_namespace``. Defaults
            to a fresh ``InProcessTurnBuffer`` (the v0.2 substrate; no
            persistent cross-session store).
        quarantine_agents: optional ``{gate_id: Agent}`` map wiring a
            slow-tier Q-LLM per gate. When provided for a gate, that
            gate's ``check_slow`` runs after ``check_fast``. Default:
            empty -> fast-tier only (offline).
        settings: ``VistaGuardSettings`` used to build the per-session
            ``TrustScorer``. The trust scorer is only *active* in the
            ``full`` config (``config.trust_active``); intermediate
            cumulative configs run with it disabled.
    """

    def __init__(
        self,
        *,
        memory_store: MemoryStore | None = None,
        quarantine_agents: dict[str, Any] | None = None,
        settings: VistaGuardSettings | None = None,
    ) -> None:
        self._memory = memory_store or InProcessTurnBuffer()
        self._quarantine_agents = dict(quarantine_agents or {})
        self._settings = settings or VistaGuardSettings(enabled=True)

    async def run(self, instance: Instance, config: AblationConfig) -> Trace:
        """Run ``instance`` under ``config`` and return its ``Trace``."""
        stack = build_gate_stack(config)
        recorder = TraceRecorder(
            instance_id=instance.instance_id,
            boundary=instance.boundary,
            template=instance.template,
            config_name=config.name,
            kind=instance.kind,
        )
        namespace = instance.effective_namespace
        # Start each run from a clean namespace so re-runs (and the next
        # config in the ablation sweep) don't read a previous run's plant.
        self._memory.clear(namespace)

        # The trust scorer is part of full VISTAGuard; in intermediate
        # cumulative configs it is built disabled so it neither terminates
        # sessions nor feeds tier-based gate decisions.
        trust_settings = self._settings.model_copy(
            update={"enabled": config.trust_active}
        )
        for session in instance.sessions:
            registry = CapabilityRegistry()
            trust = TrustScorer(trust_settings)
            ctx = GateContext(capability_registry=registry, trust_scorer=trust)
            terminated = await self._run_session(
                session, instance, config, stack, ctx, namespace, recorder
            )
            # End-of-session capability snapshot for the scorer's
            # hard-win check.
            recorder.snapshot_tags(
                [(vid, _tag_fields(tag)) for vid, tag in registry.find()]
            )
            if terminated:
                recorder.mark_terminated()
                break

        return recorder.finalize()

    # -------------------------------------------------------------
    # Session / action execution
    # -------------------------------------------------------------

    async def _run_session(
        self,
        session: Session,
        instance: Instance,
        config: AblationConfig,
        stack: GateStack,
        ctx: GateContext,
        namespace: str,
        recorder: TraceRecorder,
    ) -> bool:
        """Run one session's turns. Returns True if it terminated."""
        for turn_index, turn in enumerate(session.turns):
            for action_index, action in enumerate(turn.actions):
                self._register_capability(action, ctx.capability_registry)
                record = await self._run_action(
                    action,
                    session_id=session.session_id,
                    turn_index=turn_index,
                    action_index=action_index,
                    stack=stack,
                    ctx=ctx,
                    namespace=namespace,
                )
                recorder.record_action(record)
                if ctx.trust_scorer.terminated:
                    return True
        return False

    def _register_capability(
        self, action: Action, registry: CapabilityRegistry
    ) -> None:
        """Register an action's declared capability tag pre-gate."""
        if action.capability is not None:
            registry.tag(action.capability.value_id, _spec_to_tag(action.capability))

    async def _run_action(
        self,
        action: Action,
        *,
        session_id: str,
        turn_index: int,
        action_index: int,
        stack: GateStack,
        ctx: GateContext,
        namespace: str,
    ) -> ActionRecord:
        # Memory writes/reads touch the substrate before (or instead of)
        # any gate.
        read_contents: list[str] = []
        if action.kind is ActionKind.MEMORY_WRITE:
            self._do_memory_write(action, session_id, namespace)
        elif action.kind is ActionKind.MEMORY_READ:
            read_contents = self._do_memory_read(action, namespace, ctx)

        gate_id = action.resolved_gate()
        gate = stack.gate_for(gate_id)

        # No live defender for this action -> it passes, recorded as such.
        if gate is None:
            return self._record(
                action,
                session_id,
                turn_index,
                action_index,
                gate_id,
                defender_live=False,
                decision=GateDecision(allow=True, reason="no live defender"),
            )

        payload = self._build_gate_payload(action, read_contents)
        decision = await gate.check_fast(payload, ctx)
        # Optional slow tier when a Q-LLM is wired for this gate.
        q_agent = self._quarantine_agents.get(gate_id or "")
        if decision.allow and q_agent is not None:
            ctx.quarantine_agent = q_agent
            decision = await gate.check_slow(payload, ctx, decision)
            ctx.quarantine_agent = None

        return self._record(
            action,
            session_id,
            turn_index,
            action_index,
            gate_id,
            defender_live=True,
            decision=decision,
        )

    # -------------------------------------------------------------
    # Memory ops
    # -------------------------------------------------------------

    def _do_memory_write(
        self, action: Action, session_id: str, namespace: str
    ) -> None:
        cap = action.capability.as_dict() if action.capability is not None else None
        self._memory.append(
            MemoryRecord(
                namespace=namespace,
                session_id=session_id,
                key=str(action.payload.get("key", "")),
                content=str(action.payload.get("content", "")),
                capability=cap,
            )
        )

    def _do_memory_read(
        self, action: Action, namespace: str, ctx: GateContext
    ) -> list[str]:
        """Read planted records, re-registering their capability tags.

        Re-registering on read is the structural property MINJA tries to
        defeat: a value planted under ``taint=True`` in session A carries
        ``taint=True`` when session B reads it, because the tag rode
        along in the store -- the LLM can't strip it.
        """
        key = action.payload.get("key")
        records = self._memory.read(namespace, key if key else None)
        contents: list[str] = []
        for rec in records:
            contents.append(rec.content)
            if rec.capability is not None:
                spec = CapabilitySpec(
                    value_id=rec.capability.get("value_id", f"mem:{rec.key}"),
                    source=rec.capability.get("source", "rag:memory"),
                    dual_use=rec.capability.get("dual_use", "none"),
                    taint=bool(rec.capability.get("taint", True)),
                )
                ctx.capability_registry.tag(spec.value_id, _spec_to_tag(spec))
        return contents

    # -------------------------------------------------------------
    # Payload shaping
    # -------------------------------------------------------------

    def _build_gate_payload(
        self, action: Action, read_contents: list[str]
    ) -> dict[str, Any]:
        """Build the gate-ready payload for an action.

        Authors usually supply ``action.payload`` already shaped for the
        gate (e.g. ``{"user_prompt": ...}`` for G1). Memory reads are the
        exception: their content comes from the store, so the runner
        folds the retrieved text into the query G3 inspects.
        """
        if action.kind is ActionKind.MEMORY_READ:
            override = action.payload.get("gate_payload")
            if isinstance(override, dict):
                return dict(override)
            return {
                "kb_slug": action.payload.get("kb_slug", "memory"),
                "query": "\n".join(read_contents),
            }
        # Defensive copy so a gate that rewrites args can't mutate the
        # authored instance.
        return dict(action.payload)

    # -------------------------------------------------------------
    # Recording
    # -------------------------------------------------------------

    def _record(
        self,
        action: Action,
        session_id: str,
        turn_index: int,
        action_index: int,
        gate_id: str | None,
        *,
        defender_live: bool,
        decision: GateDecision,
    ) -> ActionRecord:
        cap_fields = (
            action.capability.as_dict() if action.capability is not None else None
        )
        return ActionRecord(
            session_id=session_id,
            turn_index=turn_index,
            action_index=action_index,
            kind=action.kind.value,
            gate=gate_id,
            defender_live=defender_live,
            is_attack=action.is_attack,
            is_utility=action.is_utility,
            label=action.label,
            allowed=decision.allow,
            blocked_by=gate_id if not decision.allow else None,
            reason=decision.reason,
            incident_level=decision.incident_level,
            value_id=action.capability.value_id if action.capability else None,
            capability=cap_fields,
        )
