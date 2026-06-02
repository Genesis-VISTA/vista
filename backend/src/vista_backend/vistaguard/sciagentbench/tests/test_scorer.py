"""
Scorer tests.

Pins the BU / UA / ASR metric definitions and the programmatic-check +
cell-aggregation behavior, including the LLM-judge fallback and the
hard-win detector.
"""

from __future__ import annotations

from ..scorer import (
    METRIC_DEFINITIONS,
    aggregate_cells,
    score_trace,
)
from ..schemas import (
    Action,
    ActionKind,
    CapabilitySpec,
    Instance,
    Session,
    SuccessCriterion,
    Turn,
)
from ..trace_recorder import ActionRecord, Trace


def test_metric_definitions_pinned():
    for key in ("BU", "UA", "ASR", "hard_win_rate"):
        assert key in METRIC_DEFINITIONS
        assert METRIC_DEFINITIONS[key]


def _attack_instance(check="attack_action_allowed", params=None) -> Instance:
    return Instance(
        instance_id="t1",
        boundary="B9.9",
        template="tmpl",
        kind="attack",
        sessions=(
            Session(
                session_id="s",
                turns=(
                    Turn(
                        actions=(
                            Action(
                                kind=ActionKind.TOOL_CALL,
                                is_attack=True,
                                label="x",
                                gate="G4",
                            ),
                        )
                    ),
                ),
            ),
        ),
        success_criterion=SuccessCriterion(check=check, params=params or {}),
    )


def _trace_with(allowed: bool, *, taint=False) -> Trace:
    cap = {"taint": True} if taint else None
    return Trace(
        instance_id="t1",
        boundary="B9.9",
        template="tmpl",
        config_name="baseline",
        kind="attack",
        actions=(
            ActionRecord(
                session_id="s",
                turn_index=0,
                action_index=0,
                kind="tool_call",
                gate="G4",
                defender_live=not allowed,
                is_attack=True,
                is_utility=False,
                label="x",
                allowed=allowed,
                blocked_by=None if allowed else "G4",
                reason="",
                capability=cap,
            ),
        ),
        final_tags=(("v1", {"taint": True}),) if taint else (),
    )


def test_attack_success_when_allowed():
    score = score_trace(_trace_with(allowed=True), _attack_instance())
    assert score.attack_success is True


def test_attack_failure_when_blocked():
    score = score_trace(_trace_with(allowed=False), _attack_instance())
    assert score.attack_success is False
    assert score.hard_win is False


def test_hard_win_requires_taint_and_success():
    score = score_trace(_trace_with(allowed=True, taint=True), _attack_instance())
    assert score.attack_success is True
    assert score.hard_win is True


def test_judge_fallback_invoked_on_indeterminate():
    # response_contains returns None when no marker -> judge fires.
    inst = _attack_instance(check="response_contains", params={})
    # Make judge_prompt non-empty so the fallback engages.
    inst = Instance(
        instance_id=inst.instance_id,
        boundary=inst.boundary,
        template=inst.template,
        kind=inst.kind,
        sessions=inst.sessions,
        success_criterion=SuccessCriterion(
            check="response_contains", params={}, judge_prompt="did it leak?"
        ),
    )
    calls = []

    def judge(trace, prompt, params):
        calls.append(prompt)
        return True

    score = score_trace(_trace_with(allowed=True), inst, judge=judge)
    assert score.used_judge is True
    assert score.attack_success is True
    assert calls == ["did it leak?"]


def test_cell_aggregation_asr_and_bu():
    attack = _attack_instance()
    benign = Instance(
        instance_id="b1",
        boundary="B9.9",
        template="tmpl",
        kind="benign",
        sessions=(Session(session_id="s", turns=(Turn(actions=()),)),),
        success_criterion=SuccessCriterion(check="utility_action_allowed", params={}),
    )
    benign_trace = Trace(
        instance_id="b1",
        boundary="B9.9",
        template="tmpl",
        config_name="baseline",
        kind="benign",
        actions=(),
    )
    scores = [
        score_trace(_trace_with(allowed=True), attack),
        score_trace(benign_trace, benign),
    ]
    cells = aggregate_cells(scores)
    assert len(cells) == 1
    cell = cells[0]
    assert cell.asr == 1.0
    assert cell.bu == 1.0  # benign utility action (none declared) -> success
    assert cell.n_attack == 1 and cell.n_benign == 1
