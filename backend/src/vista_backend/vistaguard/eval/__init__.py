"""
VISTAGuard synthetic evaluation harnesses.
"""

from .attacks import ATTACK_PROFILES, AttackProfile, generate_attack
from .corpus import BenignQuery, Chunk, generate_corpus
from .g1_attacks import (
    ATTACK_GENERATORS as G1_ATTACK_GENERATORS,
    DISPLAY_NAMES as G1_DISPLAY_NAMES,
    G1AttackScenario,
    generate_attacks as generate_g1_attacks,
    generate_benign_workload as generate_g1_benign_workload,
)
from .g1_report import format_g1_report
from .g1_runner import (
    DEFAULT_G1_CONFIGS,
    G1CellResult,
    G1EvalConfig,
    G1EvaluationResult,
    G1FprResult,
    run_g1_evaluation,
)
from .g2_attacks import (
    ATTACK_GENERATORS as G2_ATTACK_GENERATORS,
    DISPLAY_NAMES as G2_DISPLAY_NAMES,
    G2AttackScenario,
    generate_attacks as generate_g2_attacks,
    generate_benign_workload as generate_g2_benign_workload,
)
from .g2_report import format_g2_report
from .g2_runner import (
    DEFAULT_G2_CONFIGS,
    G2CellResult,
    G2DucResult,
    G2EvalConfig,
    G2EvaluationResult,
    run_g2_evaluation,
)
from .g4_attacks import (
    ATTACK_GENERATORS as G4_ATTACK_GENERATORS,
    DISPLAY_NAMES as G4_DISPLAY_NAMES,
    G4AttackScenario,
    generate_attacks as generate_g4_attacks,
    generate_benign_workload as generate_g4_benign_workload,
)
from .g4_report import format_g4_report
from .g4_runner import (
    DEFAULT_G4_CONFIGS,
    G4CellResult,
    G4EvalConfig,
    G4EvaluationResult,
    G4FprResult,
    run_g4_evaluation,
)
from .g5_attacks import (
    ATTACK_FAMILIES as G5_ATTACK_FAMILIES,
    ATTACK_GENERATORS as G5_ATTACK_GENERATORS,
    DISPLAY_NAMES as G5_DISPLAY_NAMES,
    G5AttackScenario,
    generate_attacks as generate_g5_attacks,
    generate_benign_workload as generate_g5_benign_workload,
)
from .g5_report import format_g5_report
from .g5_runner import (
    DEFAULT_G5_CONFIGS,
    G5CellResult,
    G5EvalConfig,
    G5EvaluationResult,
    G5FprResult,
    build_eval_policy as build_g5_eval_policy,
    run_g5_evaluation,
)
from .report import format_report
from .runner import EvaluationConfig, EvaluationResult, run_evaluation

__all__ = [
    "ATTACK_PROFILES",
    "AttackProfile",
    "BenignQuery",
    "Chunk",
    "DEFAULT_G1_CONFIGS",
    "DEFAULT_G2_CONFIGS",
    "DEFAULT_G4_CONFIGS",
    "DEFAULT_G5_CONFIGS",
    "EvaluationConfig",
    "EvaluationResult",
    "G1_ATTACK_GENERATORS",
    "G1_DISPLAY_NAMES",
    "G1AttackScenario",
    "G1CellResult",
    "G1EvalConfig",
    "G1EvaluationResult",
    "G1FprResult",
    "G2_ATTACK_GENERATORS",
    "G2_DISPLAY_NAMES",
    "G2AttackScenario",
    "G2CellResult",
    "G2DucResult",
    "G2EvalConfig",
    "G2EvaluationResult",
    "G4_ATTACK_GENERATORS",
    "G4_DISPLAY_NAMES",
    "G4AttackScenario",
    "G4CellResult",
    "G4EvalConfig",
    "G4EvaluationResult",
    "G4FprResult",
    "G5_ATTACK_FAMILIES",
    "G5_ATTACK_GENERATORS",
    "G5_DISPLAY_NAMES",
    "G5AttackScenario",
    "G5CellResult",
    "G5EvalConfig",
    "G5EvaluationResult",
    "G5FprResult",
    "build_g5_eval_policy",
    "format_g1_report",
    "format_g2_report",
    "format_g4_report",
    "format_g5_report",
    "format_report",
    "generate_attack",
    "generate_corpus",
    "generate_g1_attacks",
    "generate_g1_benign_workload",
    "generate_g2_attacks",
    "generate_g2_benign_workload",
    "generate_g4_attacks",
    "generate_g4_benign_workload",
    "generate_g5_attacks",
    "generate_g5_benign_workload",
    "run_evaluation",
    "run_g1_evaluation",
    "run_g2_evaluation",
    "run_g4_evaluation",
    "run_g5_evaluation",
]
