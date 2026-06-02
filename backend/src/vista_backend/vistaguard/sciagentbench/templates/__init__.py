"""
SciAgentBench attack-corpus -- the active template generators.

Each module exposes ``build() -> list[Instance]`` returning that
template's instance pool (≈3-5 instances, one per variation axis). The
registry below is the single source of truth for the active corpus;
``corpus_builder`` materializes it to YAML on disk and the harness loads
those files.

The corpus is **gate-organized** (§C attack classes per boundary +
cross-cutting + the §B benign false-positive set), not a fixed grid. The
B2 tool-boundary classes are enforcement-only (out of red-team scope);
the B3.5 MINJA and B1.6 CUI-extraction templates are deferred to CHUNKS
and live under ``sciagentbench/deferred/templates/``.
"""

from __future__ import annotations

from collections.abc import Callable

from ..schemas import Instance
from . import (
    # B1 -- prompt gate (G1)
    b1_1_direct_injection,
    b1_2_credentialing,
    b1_3_multi_turn_crescendo,
    b1_4_obfuscated_encoded,
    b1_5_adversarial_suffix,
    b1_7_goal_hijack,
    b1_8_refusal_suppression,
    b1_9_attached_content,
    b1_10_dual_use_framing,
    # B3 -- RAG / corpus gate (G3)
    b3_1_corpus_poisoning,
    b3_2_embedding_space,
    b3_3_data_value_poisoning,
    b3_4_citation_forgery,
    b3_6_retrieval_coupled_extraction,
    b3_7_indirect_injection,
    b3_8_hybrid_retrieval_seam,
    # B4 -- code-execution gate (G4)
    b4_1_malicious_code,
    b4_2_network_exfil,
    b4_3_typo_squat,
    b4_4_filesystem_write_abuse,
    b4_5_correctness_sabotage,
    b4_6_create_file_abuse,
    b4_7_sensitive_file_read,
    b4_8_resource_exhaustion,
    b4_9_corpus_injected_code,
    # B5 -- HPC-job gate (G5)
    b5_1_mining,
    b5_2_credential_exfil,
    b5_3_slurm_smuggling,
    b5_4_allocation_abuse,
    b5_5_resource_dos,
    b5_6_lateral_movement_lustre,
    b5_7_prolog_epilog_injection,
    b5_8_chained_dag_escalation,
    b5_9_social_engineering_confirmation,
    b5_10_status_query_injection,
    # Cross-cutting
    xc_1_cross_boundary_chain,
    xc_2_runtime_supply_chain,
    xc_3_adaptive_seed,
    # §B benign false-positive set
    benign_workload,
)

_MODULES = (
    # B1
    b1_1_direct_injection,
    b1_2_credentialing,
    b1_3_multi_turn_crescendo,
    b1_4_obfuscated_encoded,
    b1_5_adversarial_suffix,
    b1_7_goal_hijack,
    b1_8_refusal_suppression,
    b1_9_attached_content,
    b1_10_dual_use_framing,
    # B3
    b3_1_corpus_poisoning,
    b3_2_embedding_space,
    b3_3_data_value_poisoning,
    b3_4_citation_forgery,
    b3_6_retrieval_coupled_extraction,
    b3_7_indirect_injection,
    b3_8_hybrid_retrieval_seam,
    # B4
    b4_1_malicious_code,
    b4_2_network_exfil,
    b4_3_typo_squat,
    b4_4_filesystem_write_abuse,
    b4_5_correctness_sabotage,
    b4_6_create_file_abuse,
    b4_7_sensitive_file_read,
    b4_8_resource_exhaustion,
    b4_9_corpus_injected_code,
    # B5
    b5_1_mining,
    b5_2_credential_exfil,
    b5_3_slurm_smuggling,
    b5_4_allocation_abuse,
    b5_5_resource_dos,
    b5_6_lateral_movement_lustre,
    b5_7_prolog_epilog_injection,
    b5_8_chained_dag_escalation,
    b5_9_social_engineering_confirmation,
    b5_10_status_query_injection,
    # Cross-cutting
    xc_1_cross_boundary_chain,
    xc_2_runtime_supply_chain,
    xc_3_adaptive_seed,
    # Benign
    benign_workload,
)

#: template key -> builder. Order is the corpus's canonical boundary order.
TEMPLATE_BUILDERS: dict[str, Callable[[], list[Instance]]] = {
    mod.TEMPLATE: mod.build for mod in _MODULES
}


def build_all_instances() -> list[Instance]:
    """Build all active instances across every registered template."""
    instances: list[Instance] = []
    for builder in TEMPLATE_BUILDERS.values():
        instances.extend(builder())
    return instances


__all__ = ["TEMPLATE_BUILDERS", "build_all_instances"]
