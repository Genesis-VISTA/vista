from pydantic import BaseModel, Field

class VistaGuardSettings(BaseModel):
    """All VISTAGuard config. Read from env as VISTA_BACKEND_VISTAGUARD_*."""

    enabled: bool = False
    """Master flag. False = sidecar is a no-op."""

    # Per-gate flags (default False until evaluated)
    g1_enabled: bool = False
    g2_enabled: bool = False
    g3_enabled: bool = False
    g4_enabled: bool = False
    g5_enabled: bool = False
    g6_enabled: bool = False
    g7_enabled: bool = False

    # Q-LLM
    quarantine_model: str = "ollama:llama3.2:3b"
    """PydanticAI model spec for the Q-LLM. Local serving (Ollama, vLLM)
    keeps CUI content from leaving the deployment. Override per-deployment."""
    quarantine_enabled: bool = False
    """Master slow-tier flag. When False, gates run fast-only."""

    # Self-consistency for high-stakes gates
    quarantine_self_consistency_samples: int = 1
    """Number of Q-LLM samples for high-stakes gates (G5/G6/destructive
    G2). 2 enables the two-sample agreement check"""

    # Trust scorer
    initial_trust: float = 1.0
    tier_transition_thresholds: dict[str, float] = Field(
        default_factory=lambda: {"ELEVATED": 0.7, "RESTRICTED": 0.4, "TERMINATED": 0.1}
    )
    sticky_high_stakes: bool = True
    """When True, G5-above-ceiling / G6-destructive / G3-CUI capabilities
    do not auto-recover within a session """

    # Provenance
    flowcept_enabled: bool = False
    flowcept_endpoint: str | None = None
    """When None, provenance events are logged but not shipped to Flowcept."""

    # G4 code-scanning
    semgrep_enabled: bool = False
    semgrep_config: str = "p/security-audit"

    # Contract library
    contracts_dir: str = "../vistaguard_contracts"
