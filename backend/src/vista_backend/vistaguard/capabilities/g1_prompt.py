"""
`G1PromptCapability` -- the PydanticAI-hook adapter for the G1 Prompt
Gate.

Before this migration, G1 was wired into VISTA through two bespoke
seams in ``agents.py``:

1. An *early-rejection block* at the top of ``run_stream``'s
   ``agent_stream`` that called ``sidecar.evaluate_user_prompt(...)``
   and, on deny, short-circuited with a synthetic result event.
2. A ``@agent.system_prompt`` *tier banner* that prepended
   ``[VISTAGUARD] Session tier: <TIER>`` to the system prompt when G1
   was active.

Both are now expressed as hooks on a single capability:

- ``before_run`` runs ``sidecar.evaluate_user_prompt(...)`` and, on a
  deny decision, raises `VistaGuardDeny`. ``agents.py`` catches that
  exception in the run-stream wrapper and emits the same synthetic
  terminal event (empty ``new_messages`` + zeroed ``RunUsage``) the
  early-rejection block used to emit. Keeping the deny path as an
  exception means the abort happens from inside PydanticAI's run
  machinery -- the model is never requested.
- ``before_model_request`` prepends the tier banner as the first
  ``SystemPromptPart`` of the first ``ModelRequest``. Unlike the old
  ``@agent.system_prompt`` (which only contributed to the *first*
  model call's prompt construction), this fires on *every* model
  request, so when Phase-5 trust scoring transitions the tier
  mid-run the agent sees the updated banner on the next call.

The capability delegates all policy to the existing G1 gate via the
sidecar (``evaluate_user_prompt`` already runs G1 fast + slow tiers and
records incidents). This issue is a *migration*, not a rewrite: the
gate's ``check_fast`` / ``extract_intent`` logic and its unit tests are
untouched.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING

from pydantic_ai import RunContext
from pydantic_ai.messages import ModelRequest, SystemPromptPart

from ..gates.base import GateContext, GateDecision
from ..gates.g1_prompt import G1PromptGate
from .base import VistaGuardCapability
from .exceptions import VistaGuardDeny

if TYPE_CHECKING:
    from pydantic_ai.models import ModelRequestContext


logger = logging.getLogger(__name__)


#: Prefix shared by `before_model_request` (when it writes the banner)
#: and the strip step (when it removes a stale banner before rewriting
#: it). Kept as a single constant so the write and the match can never
#: drift apart.
BANNER_PREFIX = "[VISTAGUARD] Session tier:"


class G1PromptCapability(VistaGuardCapability):
    """
    G1 Prompt Gate exposed as a PydanticAI capability.

    Registers two hooks:

    - ``before_run`` -> early-rejection (raises `VistaGuardDeny`).
    - ``before_model_request`` -> tier banner injection.

    Both no-op when the capability is disabled (`is_enabled()` is
    False), preserving the modularity contract: a sidecar built with
    the master flag or ``g1_enabled`` off behaves exactly like
    baseline VISTA.
    """

    #: Pin the enable flag explicitly. The base class would derive the
    #: same name from the gate's ``name`` ("G1" -> "g1_enabled"), but
    #: being explicit documents the binding at the capability level.
    gate_flag = "g1_enabled"

    # -----------------------------------------------------------------
    # before_run -- early rejection
    # -----------------------------------------------------------------

    async def before_run(self, ctx: RunContext) -> None:
        """
        Run G1 on the user prompt; raise `VistaGuardDeny` on a deny.

        Runs the gate's fast tier (and the slow-tier intent extraction
        when a Q-LLM is attached), records any incident, and raises
        `VistaGuardDeny` on a deny so the run aborts before any model
        request is issued. (Phase 3.5 R5 inlined this logic here from
        the now-deleted ``VistaGuardSidecar.evaluate_user_prompt``.)
        """
        if not self.is_enabled():
            return
        if self.sidecar.trust_scorer.terminated:
            # A prior SEV1 incident terminated the session; refuse every
            # subsequent run before any model request (proposal §2.3).
            raise VistaGuardDeny(self._terminated_decision())
        user_prompt = ctx.prompt
        if not isinstance(user_prompt, str):
            # VISTA always passes a plain-string prompt; a non-string
            # prompt (multimodal content sequence) is outside G1's
            # fast-tier contract, so there is nothing to evaluate.
            return

        gate = self.gate
        gate_ctx = GateContext(
            capability_registry=self.sidecar.capability_registry,
            trust_scorer=self.sidecar.trust_scorer,
            quarantine_agent=self.sidecar.quarantine_agent,
            provenance=self.sidecar.provenance,
        )
        payload = {"user_prompt": user_prompt, "attached_files": []}

        fast = await gate.check_fast(payload, gate_ctx)
        if not fast.allow:
            self._record_incident(fast)
            raise VistaGuardDeny(fast)

        # Slow-tier intent extraction (no-op when no Q-LLM is attached).
        slow: GateDecision = fast
        if isinstance(gate, G1PromptGate) and gate.intent_extraction_agent is not None:
            try:
                slow = await gate.extract_intent(payload, gate_ctx, fast)
            except Exception as exc:  # noqa: BLE001 -- defensive
                logger.warning(
                    "VISTAGuard G1 slow-tier failed (%s: %s); "
                    "falling back to fast-tier decision",
                    type(exc).__name__,
                    exc,
                )
                slow = fast

        if not slow.allow:
            self._record_incident(slow)
            raise VistaGuardDeny(slow)

        # Allowed: surface any informational incident (CUI / PII /
        # benign-intent annotations) without short-circuiting.
        self._record_incident(slow)

    def _record_incident(self, decision: GateDecision) -> None:
        if decision.incident_level is not None:
            self.sidecar.incident_manager.record(
                level=decision.incident_level,
                gate="G1",
                reason=decision.reason,
                capability_tag=decision.capability_tag,
            )

    # -----------------------------------------------------------------
    # before_model_request -- tier banner
    # -----------------------------------------------------------------

    async def before_model_request(
        self,
        ctx: RunContext,
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        """
        Prepend ``[VISTAGUARD] Session tier: <TIER>`` to the request.

        The banner becomes the first ``SystemPromptPart`` of the first
        ``ModelRequest``. Any prior banner part (from an earlier model
        request in the same run, or carried over in ``message_history``
        from a previous turn) is stripped first, so the banner is
        rewritten -- never duplicated -- and always reflects the
        *current* trust tier.
        """
        if not self.is_enabled():
            return request_context
        if self.sidecar.trust_scorer.terminated:
            # A SEV1 fired mid-run (e.g. from a G2 tool deny): abort the
            # current run at the next model-request boundary so the agent
            # stops rather than taking another turn.
            raise VistaGuardDeny(self._terminated_decision())

        messages = request_context.messages
        first = next(
            ((i, m) for i, m in enumerate(messages) if isinstance(m, ModelRequest)),
            None,
        )
        if first is None:
            # No request to annotate (should not happen during a real
            # run, but stay defensive rather than index blindly).
            return request_context

        idx, request = first
        kept_parts = [
            p
            for p in request.parts
            if not (
                isinstance(p, SystemPromptPart) and p.content.startswith(BANNER_PREFIX)
            )
        ]
        banner = SystemPromptPart(content=self._banner_text())
        messages[idx] = replace(request, parts=[banner, *kept_parts])
        return request_context

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def _banner_text(self) -> str:
        tier = self.sidecar.trust_scorer.current_tier()
        return f"{BANNER_PREFIX} {tier.value.upper()}"

    @staticmethod
    def _terminated_decision() -> GateDecision:
        """The synthetic deny used to refuse a terminated session."""
        return GateDecision(
            allow=False,
            reason=(
                "VISTAGuard: session terminated by a prior SEV1 incident; "
                "no further interaction is permitted."
            ),
            incident_level=1,
        )


__all__ = ["BANNER_PREFIX", "G1PromptCapability"]
