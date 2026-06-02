"""
B5.4 -- allocation-scope abuse, 5 patterns.

The five allocation-abuse generators from ``g5_attacks.py`` (wrong
purpose, wrong allocation, resource scale, project scope, PI mismatch),
each a ``submit_hpc_job``. G5's allocation-policy fast tier (the DSL
contracts) fires when the job exceeds or steps outside the account's
authorized scope.
"""

from __future__ import annotations

import random

from ...eval.g5_attacks import ATTACK_FAMILIES, ATTACK_GENERATORS
from ..schemas import Action, ActionKind, Instance
from ._helpers import single_action_instance

BOUNDARY = "B5.4"
TEMPLATE = "b5_4_allocation_abuse"
_FAMILY = "allocation"
_REFERENCES = ("VISTAGuard G5 allocation policy (allocation_policy.json)",)


def build() -> list[Instance]:
    rng = random.Random(504)
    instances: list[Instance] = []
    for idx, key in enumerate(ATTACK_FAMILIES[_FAMILY]):
        scenario = ATTACK_GENERATORS[key](rng)
        attack = Action(
            kind=ActionKind.TOOL_CALL,
            gate="G5",
            is_attack=True,
            label=f"allocation-scope abuse ({key})",
            payload={
                "slurm_script": scenario.slurm_script,
                "user_config": scenario.user_config,
            },
        )
        instances.append(
            single_action_instance(
                instance_id=f"b5_4_{key}_{idx:02d}",
                boundary=BOUNDARY,
                template=TEMPLATE,
                attack_action=attack,
                variation_axis=key,
                description=f"Allocation-scope abuse: {key}.",
                references=_REFERENCES,
                utility_gate="G5",
                rng=rng,
            )
        )
    return instances
