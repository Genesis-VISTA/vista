class VistaGuardSidecar:
    """Composes G1..G7 into a single hook surface for ProjectAgent."""

    def __init__(self, settings: VistaGuardSettings, project: ProjectPublic):
        self.settings = settings
        self.project = project
        self.capability_registry = CapabilityRegistry()
        self.trust_scorer = TrustScorer(settings)
        self.contracts = load_contract_library(settings.contracts_dir, project)
        self.provenance = ProvenanceEmitter(settings)
        self.gates = self._build_gates()

    def _build_gates(self) -> dict[str, Gate]:
        gates: dict[str, Gate] = {}
        if self.settings.g1_enabled:
            gates["G1"] = PromptGate(...)
        if self.settings.g2_enabled:
            gates["G2"] = ToolGate(...)
        # ... etc
        return gates

    def is_active(self) -> bool:
        return self.settings.enabled and bool(self.gates)

    async def process_tool_call(self, ctx, call_tool, tool_name, args):
        """Composable with VISTA's existing kb-scope hook."""
        if "G2" not in self.gates:
            return await call_tool(tool_name, args)
        # ... G2 fast/slow logic ...