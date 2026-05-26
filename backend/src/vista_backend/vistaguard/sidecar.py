"""
VistaGuardSidecar -- the per-session composite that `ProjectAgent`
instantiates.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from ..db.schemas import ProjectPublic
from .capabilities import CapabilityRegistry
from .config import VistaGuardSettings
from .gates.base import Gate, GateContext
from .gates.g2_tool import (
    HIGH_STAKES_FALLBACK,
    G2ToolGate,
    ToolMetadata,
    discover_high_stakes_tools,
    discover_tool_metadata,
)
from .gates.g3_hybrid import inject_hybrid_args, should_inject_hybrid
from .gates.g3_rag import (
    KB_POLICY_FILENAME,
    G3RagGate,
    KbPolicy,
    load_kb_policy,
    tag_rag_chunks,
)
from .incidents import IncidentManager
from .provenance import ProvenanceEmitter
from .tool_registry import (
    MANIFEST_FILENAME,
    ToolDescriptorRegistry,
    load_manifest,
)
from .trust import TrustScorer


logger = logging.getLogger(__name__)


# -----------------------------------------------------------------
# Type aliases
# -----------------------------------------------------------------


CallToolFn = Callable[..., Awaitable[Any]]


# -----------------------------------------------------------------
# VistaGuardSidecar
# -----------------------------------------------------------------


class VistaGuardSidecar:
    """
    Per-`ProjectAgent` composite that owns the VISTAGuard runtime
    state.

    One sidecar is constructed per `ProjectAgent.run_stream`
    invocation (i.e., per agent turn from VISTA's perspective).
    """

    def __init__(
        self,
        settings: VistaGuardSettings,
        project: ProjectPublic,
    ) -> None:
        """
        Construct a sidecar for the given project.

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

        # G2 cached MCP-tool metadata. 
        self._high_stakes_tools: frozenset[str] = HIGH_STAKES_FALLBACK
        self._tool_schemas: dict[str, dict[str, Any]] = {}
        self._high_stakes_tools_populated: bool = False

        # ETDI descriptor registry (Bhatt et al. arXiv 2506.01333).
        self._tool_registry = ToolDescriptorRegistry(
            manifest=self._load_manifest_if_present(),
        )

        # G3 corpus policy (sensitivity tiers + manifests).
        # Loaded synchronously from
        # `<contracts_dir>/<KB_POLICY_FILENAME>` if present
        self._g3_kb_policy: KbPolicy = self._load_kb_policy_if_present()

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
    def high_stakes_tools(self) -> frozenset[str]:
        """
        Tool names G2 treats as high-stakes for the current session.

        """
        return self._high_stakes_tools

    @property
    def tool_registry(self) -> ToolDescriptorRegistry:
        """
        ETDI descriptor registry holding startup-pinned hashes and
        the operator-supplied manifest (if any). 
        """
        return self._tool_registry

    @property
    def g3_kb_policy(self) -> KbPolicy:
        """
        The corpus-policy snapshot G3 uses for sensitivity tier
        and manifest checks.

        """
        return self._g3_kb_policy

    @property
    def tool_schemas(self) -> dict[str, dict[str, Any]]:
        """
        Per-tool `inputSchema` snapshots used by G2 for fast-tier
        schema validation.
        """
        return dict(self._tool_schemas)

    @property
    def high_stakes_tools_populated(self) -> bool:
        """
        True iff `populate_high_stakes_tools` has been called and
        succeeded against a live MCP server for this sidecar.
        """
        return self._high_stakes_tools_populated

    @property
    def gates(self) -> Mapping[str, Gate]:
        """
        Read-only view of the active gate set.
        """
        return self._gates

    # -----------------------------------------------------------------
    # Active-state predicates
    # -----------------------------------------------------------------

    def is_active(self) -> bool:
        """
        True iff the master flag is on AND at least one gate is in
        the active set.
        """
        return self._settings.enabled and bool(self._gates)

    def is_gate_enabled(self, name: str) -> bool:
        """
        True iff the named gate is in the active set.
        """
        return name in self._gates

    # -----------------------------------------------------------------
    # Q-LLM attachment
    # -----------------------------------------------------------------

    def attach_quarantine_agent(self, agent: Any) -> None:
        """
        Store the PydanticAI Q-LLM Agent for gates to invoke.

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

        """
        if not self.is_active():
            return await call_tool(tool_name, args)

        if (
            self._settings.g3_enabled
            and tool_name == "rag_search"
            and isinstance(args, dict)
        ):
            return await self._dispatch_rag_search(call_tool, args)

        return await call_tool(tool_name, args)

    async def _dispatch_rag_search(
        self,
        call_tool: CallToolFn,
        args: dict[str, Any],
    ) -> Any:
        """
        G3 wiring around a single `rag_search` call.
        """
        # ----- Pre-call gate check (fast tier) ---------------------
        g3 = self._gates.get("G3")
        if isinstance(g3, G3RagGate):
            kb_slug = args.get("kb_slug")
            query = args.get("query")
            if isinstance(kb_slug, str) and isinstance(query, str):
                gate_ctx = GateContext(
                    capability_registry=self._capability_registry,
                    trust_scorer=self._trust_scorer,
                )
                fast = await g3.check_fast(
                    {"kb_slug": kb_slug, "query": query},
                    gate_ctx,
                )
                if not fast.allow:
                    # Surface the denial through the same "ERROR:
                    # ..." string shape rag_mcp uses for its own
                    # error returns. The agent then sees a
                    # structured failure it can react to.
                    logger.warning(
                        "VISTAGuard G3: rag_search denied (kb_slug=%r): %s",
                        kb_slug, fast.reason,
                    )
                    return f"ERROR: {fast.reason}"

        effective_args = args
        if should_inject_hybrid(self._settings, "rag_search"):
            effective_args = inject_hybrid_args(
                args,
                alpha=self._settings.g3_hybrid_alpha,
            )

        result = await call_tool("rag_search", effective_args)

        # Post-call handling
        if not isinstance(result, str):
            return result

        kb_slug_raw = args.get("kb_slug") if isinstance(args, dict) else None
        kb_slug = str(kb_slug_raw) if kb_slug_raw else "unknown"

        slow_tier_active = (
            self._settings.quarantine_enabled
            and self._quarantine_agent is not None
        )
        g3 = self._gates.get("G3")

        if slow_tier_active and isinstance(g3, G3RagGate):
            gate_ctx = GateContext(
                capability_registry=self._capability_registry,
                trust_scorer=self._trust_scorer,
                quarantine_agent=self._quarantine_agent,
            )
            try:
                slow_decision = await g3.sanitize_chunks(
                    result, gate_ctx, kb_slug=kb_slug,
                )
            except Exception as exc:  
                # Slow-tier failure must not crash the agent.
                logger.warning(
                    "VISTAGuard G3 slow-tier failed (%s: %s); "
                    "falling back to fast-tier tag-only path",
                    type(exc).__name__,
                    exc,
                )
            else:
                if slow_decision.rewritten_result is not None:
                    # Slow tier rewrote the result; per-chunk tags
                    # tagging.
                    return slow_decision.rewritten_result
                # Slow tier ran but didn't rewrite 

        try:
            tag_rag_chunks(
                result,
                kb_slug=kb_slug,
                registry=self._capability_registry,
            )
        except Exception as exc:  
            
            logger.warning(
                "VISTAGuard G3: chunk tagging failed (%s: %s); "
                "downstream taint-propagation may be incomplete",
                type(exc).__name__,
                exc,
            )

        return result

    # -----------------------------------------------------------------
    # G2 high-stakes-tool discovery
    # -----------------------------------------------------------------

    async def populate_tool_metadata(self, mcp_url: str) -> ToolMetadata:
        """
        Refresh `high_stakes_tools` and `tool_schemas` from the MCP
        """
        metadata = await discover_tool_metadata(mcp_url)
        self._high_stakes_tools = metadata.high_stakes
        self._tool_schemas = dict(metadata.schemas)
        # `discovered` flag drives the populated state directly 
        self._high_stakes_tools_populated = metadata.discovered
        # ETDI pinning: pin the startup descriptor hash for every
        # discovered tool. 
        for tool_name, descriptor in metadata.descriptors.items():
            self._tool_registry.pin_startup(
                tool_name,
                description=descriptor.description,
                input_schema=descriptor.input_schema,
            )
        # Rebind any already-built G2 gate to the fresh snapshot so
        # the gate's view of the world matches the sidecar's. 
        g2 = self._gates.get("G2")
        if isinstance(g2, G2ToolGate):
            g2.rebind_metadata(
                high_stakes=metadata.high_stakes,
                schemas=metadata.schemas,
            )
        return metadata

    async def populate_high_stakes_tools(self, mcp_url: str) -> frozenset[str]:
        """
        Refresh `high_stakes_tools` only. 
        """
        metadata = await self.populate_tool_metadata(mcp_url)
        return metadata.high_stakes

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    def _build_gates(self) -> dict[str, Gate]:
        """
        Construct the active gate set from the per-gate flags.
        """
        gates: dict[str, Gate] = {}
        if self._settings.g2_enabled:
            gates["G2"] = G2ToolGate(
                enabled=True,
                allow_patterns=self._effective_tool_patterns(),
                high_stakes=self._high_stakes_tools,
                schemas=dict(self._tool_schemas),
                # The gate stores a *reference* to the registry
                tool_registry=self._tool_registry,
            )
        if self._settings.g3_enabled:
            
            gates["G3"] = G3RagGate(
                enabled=True,
                kb_sensitivity_tiers=self._g3_kb_policy.sensitivity_tiers,
                corpus_manifests=self._g3_kb_policy.corpus_manifests,
                query_injection_enabled=self._settings.g3_query_injection_enabled,
            )
        
        return gates

    def _load_manifest_if_present(self) -> dict[str, str]:
        """
        Read `<contracts_dir>/<MANIFEST_FILENAME>` and return the
        pinned-hashes dict, or `{}` when no manifest exists.
        """
        contracts_dir = Path(self._settings.contracts_dir)
        manifest_path = contracts_dir / MANIFEST_FILENAME
        return load_manifest(manifest_path)

    def _load_kb_policy_if_present(self) -> KbPolicy:
        """
        Read `<contracts_dir>/<KB_POLICY_FILENAME>` and return the
        loaded `KbPolicy`, or an empty policy when the file is
        absent or malformed. 
        """
        contracts_dir = Path(self._settings.contracts_dir)
        policy_path = contracts_dir / KB_POLICY_FILENAME
        return load_kb_policy(policy_path)

    def _effective_tool_patterns(self) -> list[str]:
        """
        Compute the allow-list patterns G2 enforces.
        """
        patterns = list(self._project.tools or [])
        if not self._project.knowledge_bases and "!rag_search" not in patterns:
            patterns.append("!rag_search")
        return patterns
