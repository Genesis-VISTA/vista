"""
Deferred template generators (parked for CHUNKS).

``b3_5_minja`` (multi-session memory poisoning) and ``b1_6_cui_extraction``
(CUI/sensitivity-tier extraction) are out of the v0.2 active corpus. They
are kept runnable here so the work can be revived when CHUNKS adds
persistent cross-session memory and the sensitivity tier is reinstated.
"""

from __future__ import annotations

from collections.abc import Callable

from ...schemas import Instance
from . import b1_6_cui_extraction, b3_5_minja

#: Deferred template key -> builder.
DEFERRED_TEMPLATE_BUILDERS: dict[str, Callable[[], list[Instance]]] = {
    b1_6_cui_extraction.TEMPLATE: b1_6_cui_extraction.build,
    b3_5_minja.TEMPLATE: b3_5_minja.build,
}


__all__ = ["DEFERRED_TEMPLATE_BUILDERS"]
