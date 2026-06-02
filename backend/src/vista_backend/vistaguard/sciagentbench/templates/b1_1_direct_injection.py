"""
B1.1 -- direct prompt injection, 5 axes.

Maps onto the G1 attack generators (``g1_attacks.py``), spanning the five
direct-injection axes: instruction-override, system-prompt exfiltration,
output-formatting hijack, scientific-framing, and role-impersonation.
All are caught by the G1 fast-tier regex, so ASR drops at ``+G1``.
"""

from __future__ import annotations

import random

from ...eval.g1_attacks import ATTACK_GENERATORS
from ..schemas import Action, ActionKind, Instance
from ._helpers import single_action_instance

BOUNDARY = "B1.1"
TEMPLATE = "b1_1_direct_injection"

# The five direct-injection axes, in order.
_AXES = (
    "instruction_override",
    "system_prompt_exfil",
    "output_formatting_hijack",
    "scientific_framing",
    "role_impersonation",
)

_REFERENCES = (
    "Greshake et al. arXiv:2302.12173",
    "Perez & Ribeiro arXiv:2211.09527",
)


def build() -> list[Instance]:
    rng = random.Random(101)
    instances: list[Instance] = []
    for idx, axis in enumerate(_AXES):
        scenario = ATTACK_GENERATORS[axis](rng)
        attack = Action(
            kind=ActionKind.PROMPT,
            gate="G1",
            is_attack=True,
            label=f"direct injection ({axis})",
            payload={"user_prompt": scenario.user_prompt},
        )
        instances.append(
            single_action_instance(
                instance_id=f"b1_1_{axis}_{idx:02d}",
                boundary=BOUNDARY,
                template=TEMPLATE,
                attack_action=attack,
                variation_axis=axis,
                description=f"Direct prompt injection via the {axis} axis.",
                references=_REFERENCES,
                utility_gate="G1",
                rng=rng,
            )
        )
    return instances
