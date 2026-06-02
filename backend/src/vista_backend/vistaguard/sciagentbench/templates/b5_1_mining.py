"""
B5.1 -- cryptomining SLURM job, 5 patterns.

The five cryptomining generators from ``g5_attacks.py`` (direct miner,
wget-exec, base64 launcher, multistage, library-call), each a
``submit_hpc_job`` carrying a resolved SLURM script + user_config. G5's
mining-signature fast tier fires, so ASR drops at the full config. Run
against the Phase-9 SLURM-in-Docker substrate.
"""

from __future__ import annotations

import random

from ...eval.g5_attacks import ATTACK_FAMILIES, ATTACK_GENERATORS
from ..schemas import Action, ActionKind, Instance
from ._helpers import single_action_instance

BOUNDARY = "B5.1"
TEMPLATE = "b5_1_mining"
_FAMILY = "cryptomining"
_REFERENCES = ("VISTAGuard G5 threat surface (docs/vistaguard/g5_threat_surface.md)",)


def build() -> list[Instance]:
    rng = random.Random(501)
    instances: list[Instance] = []
    for idx, key in enumerate(ATTACK_FAMILIES[_FAMILY]):
        scenario = ATTACK_GENERATORS[key](rng)
        attack = Action(
            kind=ActionKind.TOOL_CALL,
            gate="G5",
            is_attack=True,
            label=f"cryptomining SLURM job ({key})",
            payload={
                "slurm_script": scenario.slurm_script,
                "user_config": scenario.user_config,
            },
        )
        instances.append(
            single_action_instance(
                instance_id=f"b5_1_{key}_{idx:02d}",
                boundary=BOUNDARY,
                template=TEMPLATE,
                attack_action=attack,
                variation_axis=key,
                description=f"Cryptomining SLURM job: {key}.",
                references=_REFERENCES,
                utility_gate="G5",
                rng=rng,
            )
        )
    return instances
