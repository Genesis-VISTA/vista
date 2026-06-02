"""
Corpus-through-ablation tests (WI10 acceptance criteria).

Runs the full committed corpus through the 6-config B4-first ablation and
asserts: every attack class produces a populated (boundary, class) cell;
the §B benign workload passes clean through ``full`` (BU = 1.0); and the
dual-use probe records both an FP side (legit science passes) and an FN
side (the weaponization-framing class produces an attack cell).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio

from ...eval.sciagentbench_runner import run_sciagentbench_evaluation


async def _result():
    # Default loads the committed corpus through CUMULATIVE_CONFIGS.
    return await run_sciagentbench_evaluation()


async def test_every_attack_class_populates_a_cell():
    result = await _result()
    # Group cells by template; an attack template must have asr != None in
    # every config, a benign template must have bu != None.
    by_template: dict[str, list] = {}
    for cell in result.cells:
        by_template.setdefault(cell.template, []).append(cell)

    assert by_template, "no cells produced"
    for template, cells in by_template.items():
        is_benign = all(c.n_attack == 0 for c in cells)
        if is_benign:
            assert all(c.bu is not None for c in cells), f"{template}: BU unpopulated"
        else:
            assert all(c.asr is not None for c in cells), (
                f"{template}: ASR unpopulated in some config"
            )


async def test_benign_workload_clean_through_full():
    result = await _result()
    full = result.configs[-1].name
    benign_full = [
        c
        for c in result.cells
        if c.template == "benign_workload" and c.config_name == full
    ]
    assert benign_full, "no benign_workload cell at full config"
    for c in benign_full:
        assert c.bu == 1.0, f"benign workload BU={c.bu} at {full} (false block)"


async def test_dual_use_probe_records_fp_and_fn():
    result = await _result()
    full = result.configs[-1].name

    # FP side: every benign instance (incl. the legit tritium-breeding
    # probe) is allowed through full -> benign cells at BU 1.0.
    benign_scores = [s for s in result.scores if s.kind == "benign"]
    assert benign_scores, "no benign instances scored"
    assert all(s.benign_success for s in benign_scores if s.config_name == full)

    # FN side: the weaponization-framing class produces a populated attack
    # cell (its ASR is the false-negative rate the probe records).
    framing = [
        c
        for c in result.cells
        if c.template == "b1_10_dual_use_framing" and c.config_name == full
    ]
    assert framing and all(c.asr is not None for c in framing)
