"""
VistaGuardSidecar -- the per-session composite that `ProjectAgent`
instantiates.
"""

import logging
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from pydantic_ai.capabilities import AbstractCapability

from ..db.schemas import ProjectPublic
from .capabilities import (
    CapabilityRegistry,
    CapabilityTag,
    G1PromptCapability,
    G3RagCapability,
    G5HpcCapability,
)
from .config import VistaGuardSettings
from .contracts import load_contract_library
from .gates.base import Gate
from .gates.g1_prompt import (
    JAILBREAK_SIGNATURES_FILENAME,
    G1PromptGate,
    load_jailbreak_signatures,
)
from .gates.g2_tool import (
    HIGH_STAKES_FALLBACK,
    G2ToolGate,
    ToolMetadata,
    discover_high_stakes_tools,
    discover_tool_metadata,
)
from .gates.g3_rag import (
    KB_POLICY_FILENAME,
    G3RagGate,
    KbPolicy,
    load_kb_policy,
)
from .gates.g5_hpc import (
    ALLOCATION_POLICY_FILENAME,
    AllocationLimits,
    AllocationPolicy,
    G5HpcJobGate,
    load_allocation_policy,
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

        # Set by `build_capabilities(quarantine_agent=...)`; gate
        # capabilities read it via the `quarantine_agent` property.
        self._quarantine_agent: Any | None = None

        # Phase-5 contract library: the builtin contracts plus any
        # operator-supplied ones under `settings.contracts_dir`. Gates'
        # slow tiers consult this via `GateContext.contracts`.
        self._contracts = load_contract_library(settings.contracts_dir)

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

        # G5 (Phase 4) human-approval side-channel: maps a tool_call_id to
        # the fast-tier decision metadata the approval UI renders. Written
        # by `G5HpcCapability` when it defers a submission, read + cleared
        # by the approval emitter via `note_approval_outcome`.
        self._pending_approval_metadata: dict[str, dict[str, Any]] = {}
        # Sticky-on-decline counter (Phase-5 sticky-capability-table
        # integration point): incremented when a user declines an
        # approval, without firing a security incident.
        self._sticky_decline_count: int = 0

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
        """The Q-LLM agent set by `build_capabilities`, or None."""
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
    # G5 human-approval side-channel (Phase 4)
    # -----------------------------------------------------------------

    @property
    def pending_approval_metadata(self) -> dict[str, dict[str, Any]]:
        """
        Per-session map of ``tool_call_id`` -> gate decision metadata for
        tool calls awaiting human approval. `G5HpcCapability` writes the
        fast-tier summary here before deferring a ``submit_hpc_job`` so the
        approval emitter can surface it to the user.
        """
        return self._pending_approval_metadata

    @property
    def sticky_decline_count(self) -> int:
        """
        Number of approvals the user has declined this session. The
        Phase-5 sticky-capability table will consume this; Phase-4 records
        it (and a registry mark) without firing an incident.
        """
        return self._sticky_decline_count

    def note_approval_outcome(self, tool_call_id: str, *, approved: bool) -> None:
        """
        Record the outcome of a human-approval request and clear its
        pending metadata.

        On a *decline* of a G5-gated call this is the Phase-5
        sticky-capability-table integration point: it increments the
        sticky-decline counter and writes a sticky `CapabilityTag` to the
        registry *without* firing a security incident -- a user saying
        "no" is not a gate violation, but the sticky semantics (the
        capability does not auto-recover within the session) still apply,
        which breaks the probe-then-strike oscillation primitive.
        """
        metadata = self._pending_approval_metadata.pop(tool_call_id, None)
        if approved or metadata is None or metadata.get("gate") != "G5":
            return
        self._sticky_decline_count += 1
        self._capability_registry.tag(
            f"sticky:decline:{tool_call_id}",
            CapabilityTag(
                source="tool:submit_hpc_job",
                taint=True,
                metadata={
                    "sticky": True,
                    "g5_check": "user_decline",
                    "reason": "user declined HPC job submission at approval",
                },
            ),
        )

    # -----------------------------------------------------------------
    # Q-LLM attachment
    # -----------------------------------------------------------------

    # -----------------------------------------------------------------
    # Capability factory
    # -----------------------------------------------------------------

    def build_capabilities(
        self,
        *,
        quarantine_agent: Any | None = None,
        intent_extraction_agent: Any | None = None,
        code_intent_extraction_agent: Any | None = None,
    ) -> list[AbstractCapability]:
        """
        Build the VISTAGuard capability list for `Agent(capabilities=...)`.

        This is the sidecar's role under Phase 3.5: a factory that wraps
        the built gates as PydanticAI capabilities. Every gate's runtime
        behavior now lives on its capability's hooks; the sidecar no
        longer mediates tool calls or prompts itself.

        The Q-LLM agents are passed in (agents.py builds them from the
        top-level model spec): ``quarantine_agent`` is stored so gate
        capabilities can read it via ``sidecar.quarantine_agent``,
        ``intent_extraction_agent`` is forwarded to the G1 gate's slow
        tier, and ``code_intent_extraction_agent`` is forwarded to the G5
        gate's slow tier (G4 and G5 share it).

        Returns capabilities for the currently-wired gates (G1, G3, G5).
        G2 and G4 have capabilities but stay unwired until their
        Phase-6 evaluation gate; an empty gate set (flag-off) yields an
        empty list, keeping the agent byte-identical to baseline VISTA.
        """
        self._quarantine_agent = quarantine_agent

        g1 = self._gates.get("G1")
        if isinstance(g1, G1PromptGate) and intent_extraction_agent is not None:
            g1.attach_intent_extraction_agent(intent_extraction_agent)

        capabilities: list[AbstractCapability] = []
        if g1 is not None:
            capabilities.append(G1PromptCapability(self, g1, self._settings))
        g3 = self._gates.get("G3")
        if g3 is not None:
            capabilities.append(G3RagCapability(self, g3, self._settings))
        g5 = self._gates.get("G5")
        if g5 is not None:
            if isinstance(g5, G5HpcJobGate) and code_intent_extraction_agent is not None:
                # G4 and G5 share the code-intent Q-LLM (the prompt shape
                # is "what is this code/job trying to do?").
                g5.attach_code_intent_extraction_agent(code_intent_extraction_agent)
            capabilities.append(G5HpcCapability(self, g5, self._settings))
        return capabilities

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
        if self._settings.g1_enabled:
            # Operator-supplied signatures live at
            # `<contracts_dir>/jailbreak_signatures.txt`. 
            override_path = (
                Path(self._settings.contracts_dir) / JAILBREAK_SIGNATURES_FILENAME
            )
            if override_path.exists():
                patterns = load_jailbreak_signatures(override_path)
            else:
                # `None` -> the gate loads the bundled defaults.
                patterns = None
            gates["G1"] = G1PromptGate(
                enabled=True,
                jailbreak_patterns=patterns,
                intent_self_consistency_samples=(
                    self._settings.quarantine_self_consistency_samples
                ),
            )
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
        if self._settings.g5_enabled:
            gates["G5"] = G5HpcJobGate(
                enabled=True,
                allocation_policy=self._load_g5_policy_if_present(),
                chained_job_dag_enabled=self._settings.g5_chained_job_dag_enabled,
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

    def _load_g5_policy_if_present(self) -> AllocationPolicy:
        """
        Build the G5 `AllocationPolicy` from (in precedence order) the
        operator policy file, the settings-level ceilings, and the bundled
        defaults.

        The policy file is read from `g5_allocation_policy_path` when set,
        otherwise from `<contracts_dir>/<ALLOCATION_POLICY_FILENAME>`. A
        missing / unreadable / malformed / wrong-version file falls back to
        the bundled defaults with a WARNING (`load_allocation_policy`). When
        the file supplies no `allocations` map, `g5_resource_ceilings` from
        settings is used to build one. Finally, `g5_binary_denylist` from
        settings is unioned into the denylist (the bundled IOC list is
        always in effect).
        """
        if self._settings.g5_allocation_policy_path:
            policy_path = Path(self._settings.g5_allocation_policy_path)
        else:
            policy_path = Path(self._settings.contracts_dir) / ALLOCATION_POLICY_FILENAME
        policy = load_allocation_policy(policy_path)

        # Settings-level ceilings fill in when the file supplies none.
        if not policy.allocations_enforced and self._settings.g5_resource_ceilings:
            allocations: dict[str, AllocationLimits] = {}
            for name, limits in self._settings.g5_resource_ceilings.items():
                if isinstance(name, str) and isinstance(limits, dict):
                    allocations[name] = AllocationLimits.from_dict(limits)
            if allocations:
                policy = replace(policy, allocations=allocations)

        # Operator-supplied additional mining IOCs augment the denylist.
        if self._settings.g5_binary_denylist:
            extra = {str(b).lower() for b in self._settings.g5_binary_denylist}
            policy = replace(
                policy, binary_denylist=policy.binary_denylist | extra
            )

        return policy

    def _effective_tool_patterns(self) -> list[str]:
        """
        Compute the allow-list patterns G2 enforces.
        """
        patterns = list(self._project.tools or [])
        if not self._project.knowledge_bases and "!rag_search" not in patterns:
            patterns.append("!rag_search")
        return patterns
