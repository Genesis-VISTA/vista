"""
VISTAGuard sidecar configuration.

This module defines `VistaGuardSettings`, a Pydantic model holding every
toggle and tunable for the VISTAGuard security sidecar. It is mounted on
the top-level `Settings` as a nested field, so the entire configuration
is reachable as `settings.vistaguard.*` and overridable from the
environment with `VISTA_BACKEND_VISTAGUARD__<FIELD>=...` (double
underscore acts as the nested delimiter -- see `Settings.model_config`
in `vista_backend.config`).

By default every flag here is False, including the master `enabled`
flag. The sidecar's runtime hooks are written so that with `enabled=False`
they are byte-identical no-ops; this is the contract that VISTAGuard is
genuinely modular (see the flag-off regression test).

The fields are grouped to match the phasing in the integration plan:

- `enabled` and `g{N}_enabled` are the per-gate toggles used everywhere.
- `quarantine_*` configures the Q-LLM (Phase 1).
- `initial_trust`, `tier_transition_thresholds`, `sticky_high_stakes`
  configure the trust scorer (Phase 5).
- `flowcept_*` configures the provenance bus (Phase 0 logging,
  Phase 7 Flowcept shipping).
- `semgrep_*` configures G4 (Phase 3).
- `contracts_dir` configures the contract library (Phase 5).

Later phases may add fields here; the convention is that every field
defaults to a value that keeps the system off / minimal so that adding
a new field never changes behavior of existing deployments.
"""

from pydantic import BaseModel, Field


class VistaGuardSettings(BaseModel):
    """
    All VISTAGuard configuration.

    Read from env as `VISTA_BACKEND_VISTAGUARD__<FIELD>=...` thanks to the
    `env_nested_delimiter="__"` setting on the parent `Settings` class.
    """

    # -----------------------------------------------------------------
    # Master and per-gate toggles
    # -----------------------------------------------------------------

    enabled: bool = False
    """
    Master toggle. When False, `VistaGuardSidecar` constructs but does
    not invoke any gate logic, and its `process_tool_call` is a strict
    pass-through. The Q-LLM agent is not instantiated. The flag-off path
    is verified byte-identical to baseline VISTA by the regression test.
    """

    g1_enabled: bool = False
    """Prompt Gate. Enables prompt-side checks in `run_stream` and the
    tier-banner system prompt. Phase 3."""

    g2_enabled: bool = False
    """Tool Gate. Enables tool-call gating inside `process_tool_call`.
    Phase 1."""

    g3_enabled: bool = False
    """RAG / Memory Gate. Enables sanitization of `rag_search` returns.
    Phase 2."""

    g4_enabled: bool = False
    """Code Gate. Enables semantic-pattern scanning of code emitted to
    the sandbox (`run_bash`, `create_file`). Phase 3."""

    g5_enabled: bool = False
    """HPC Job Gate. Enables gating of `submit_hpc_job` and related
    HPC tool calls. Phase 4."""

    g6_enabled: bool = False
    """Instrument Gate. No-op in VISTAPhase 8."""

    g7_enabled: bool = False
    """Agent / Federation Gate. No-op in VISTA"""

    # -----------------------------------------------------------------
    # Quarantine (Q-LLM) configuration -- Phase 1+
    # -----------------------------------------------------------------

    quarantine_enabled: bool = False
    """
    Master slow-tier toggle. When False, every gate runs fast-only
    regardless of which gates are enabled. This is the cheapest way to
    smoke-test VISTAGuard in production: enable the deterministic
    checks before paying the Q-LLM serving cost.
    """

    quarantine_model: str = "ollama:llama3.2:3b"
    """
    PydanticAI model spec for the Q-LLM (see the `pydantic-ai` skill
    for the `provider:model-id` format). Local serving (Ollama, vLLM)
    keeps CUI content from leaving the deployment; override per
    deployment if a hosted model is acceptable for the project's
    sensitivity tier.
    """

    quarantine_self_consistency_samples: int = 1
    """
    Number of Q-LLM samples for high-stakes gates (G5, G6, and
    destructive-annotated G2 calls). Bump to 2 to enable the
    two-sample agreement check from the proposal's adaptive-attack
    discussion (§8.2). Default 1 to keep latency low until the
    deployment has measured the Q-LLM's per-call latency.
    """

    # -----------------------------------------------------------------
    # Trust scorer and tier policy -- Phase 5
    # -----------------------------------------------------------------

    initial_trust: float = 1.0
    """Starting trust score for a new session. Range [0.0, 1.0]."""

    tier_transition_thresholds: dict[str, float] = Field(
        default_factory=lambda: {
            "ELEVATED": 0.7,
            "RESTRICTED": 0.4,
            "TERMINATED": 0.1,
        }
    )
    """
    Per-tier trust thresholds. A session whose trust score falls below
    the value for a tier transitions into that tier. Defaults are
    conservative; per-deployment calibration against a held-out benign
    workload is recommended before tightening.
    """

    sticky_high_stakes: bool = True
    """
    When True (default), high-stakes capabilities -- G6 destructive
    commands, G5 above-ceiling resource requests, G3 CUI-tier corpora
    -- do not auto-recover within a session once denied. Only an
    explicit re-authentication event clears the stickiness. This
    breaks the probe-then-strike oscillation-attack primitive by
    design. Set False only when running the oscillation-attack
    benchmark.
    """

    # -----------------------------------------------------------------
    # Provenance / Flowcept integration -- Phase 0 logging,
    # Phase 7 Flowcept shipping
    # -----------------------------------------------------------------

    flowcept_enabled: bool = False
    """
    When True, ship provenance events to Flowcept. When False,
    `ProvenanceEmitter` still serializes events but writes them to a
    log file rather than to the Flowcept broker. False is the safe
    default for development.
    """

    flowcept_endpoint: str | None = None
    """
    Flowcept broker endpoint. Required when `flowcept_enabled=True`;
    `ProvenanceEmitter` raises at construction time if Flowcept is
    enabled without an endpoint (fail fast).
    """

    provenance_log_path: str | None = None
    """
    Filesystem path for the JSONL provenance log used by
    `ProvenanceEmitter` when `flowcept_enabled=False`. When None
    (default), provenance events are emitted via the module logger
    instead of a dedicated file -- ops can still capture them by
    routing the `vista_backend.vistaguard.provenance` logger to the
    desired sink. When set, the emitter appends one JSON line per
    event to this path and flushes after each write so audit data
    survives a SIGKILL. Phase 5-6 will layer AU-9 tamper-evidence
    on top of this same path; Phase 7's Flowcept broker emission
    is enabled via `flowcept_enabled`.
    """

    # -----------------------------------------------------------------
    # G4 code scanning -- Phase 3
    # -----------------------------------------------------------------

    semgrep_enabled: bool = False
    """
    Enable Semgrep-based semantic scanning of LLM-emitted code at G4.
    Requires the optional `semgrep` dependency (install with
    `pip install vista-backend[vistaguard-g4]`). When False, G4 runs
    slow-tier only (Q-LLM intent extraction).
    """

    semgrep_config: str = "p/security-audit"
    """
    Semgrep ruleset spec. Default is the community security-audit
    ruleset; VISTAGuard-specific rules layered on top live in
    `vistaguard/contracts/semgrep/`.
    """

    # -----------------------------------------------------------------
    # Contract library -- Phase 5
    # -----------------------------------------------------------------

    contracts_dir: str = "../vistaguard_contracts"
    """
    Filesystem path to the scientist-authored contract library. The
    runtime loads contracts from this directory at startup; the
    directory is intended to live in a separate repository so domain
    scientists can contribute via PRs without touching VISTAGuard
    code. When the directory is empty or missing, VISTAGuard runs
    with an empty contract registry and gates that consult the
    registry behave as if no contract applies.
    """