"""
B4.1 -- malicious-code patterns.

Five G4 malicious-code classes written via ``create_file``:
reverse-shell, dynamic eval/exec, pickle RCE, cryptominer drop-and-run,
and a disguised network DoS (the last two are the gaps this work item
adds to ``g4_attacks.py``). All fire the G4 fast-tier AST/regex rules,
so ASR drops at the ``+...+G4`` config.
"""

from __future__ import annotations

import random

from ...eval.g4_attacks import ATTACK_GENERATORS
from ..schemas import Action, ActionKind, Instance
from ._helpers import single_action_instance

BOUNDARY = "B4.1"
TEMPLATE = "b4_1_malicious_code"

_CLASSES = (
    "reverse_shell",
    "eval_exec",
    "pickle_rce",
    "cryptomining",
    "network_dos_disguised",
)

_REFERENCES = ("CWE-94 (code injection)", "CWE-502 (deserialization)")


def build() -> list[Instance]:
    rng = random.Random(401)
    instances: list[Instance] = []
    for idx, cls in enumerate(_CLASSES):
        scenario = ATTACK_GENERATORS[cls](rng)
        attack = Action(
            kind=ActionKind.TOOL_CALL,
            gate="G4",
            is_attack=True,
            label=f"malicious code ({cls})",
            payload={"tool_name": scenario.tool_name, "args": scenario.args},
        )
        instances.append(
            single_action_instance(
                instance_id=f"b4_1_{cls}_{idx:02d}",
                boundary=BOUNDARY,
                template=TEMPLATE,
                attack_action=attack,
                variation_axis=cls,
                description=f"Malicious-code pattern: {cls}.",
                references=_REFERENCES,
                utility_gate="G4",
                rng=rng,
            )
        )
    return instances
