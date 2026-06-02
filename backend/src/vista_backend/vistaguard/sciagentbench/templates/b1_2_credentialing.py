"""
B1.2 -- scientific-credentialing jailbreak (slow-tier defended).

Five credential-laundering prompts from ``g1_attacks._DUAL_USE_TEMPLATES``.
These carry no fast-tier regex signal -- the defense is the G1 slow-tier
Q-LLM, which the offline harness does not run by default. So under the
cumulative ablation these show the honest *fast-tier-only* ASR (high);
wiring a quarantine agent into the SessionRunner engages the defense.

NB: the credentialing-premise wording is held to PI/institutional
sign-off -- see ``docs/vistaguard/b1_2_credentialing_signoff.md``. The
wording is surrogate (the dual-use ask is abstracted; no actionable
payload).
"""

from __future__ import annotations

import random

from ...eval.g1_attacks import _DUAL_USE_TEMPLATES
from ..schemas import Action, ActionKind, Instance
from ._helpers import single_action_instance

BOUNDARY = "B1.2"
TEMPLATE = "b1_2_credentialing"

_REFERENCES = (
    "Wei et al. arXiv:2307.02483 (jailbreak via competing objectives)",
    "docs/vistaguard/b1_2_credentialing_signoff.md",
)


def build() -> list[Instance]:
    rng = random.Random(102)
    instances: list[Instance] = []
    for idx, prompt in enumerate(_DUAL_USE_TEMPLATES):
        attack = Action(
            kind=ActionKind.PROMPT,
            gate="G1",
            is_attack=True,
            label="credential-laundering jailbreak",
            payload={"user_prompt": prompt},
        )
        instances.append(
            single_action_instance(
                instance_id=f"b1_2_credentialing_{idx:02d}",
                boundary=BOUNDARY,
                template=TEMPLATE,
                attack_action=attack,
                variation_axis="credentialing_premise",
                description=(
                    "Scientific-credentialing jailbreak: a clearance premise "
                    "launders a dual-use request past the fast tier. "
                    "Slow-tier (Q-LLM) defended; surrogate wording."
                ),
                references=_REFERENCES,
                utility_gate="G1",
                rng=rng,
            )
        )
    return instances
