"""
Corpus integrity tests (WI10).

Pins that every registered §C attack-class template (and the §B benign
workload) builds a non-trivial instance pool, that the committed YAML on
disk round-trips, and that every action routes to a real gate.
"""

from __future__ import annotations

from pathlib import Path

from ..corpus_builder import CORPUS_DIR
from ..instance_loader import load_instances
from ..schemas import ActionKind
from ..templates import TEMPLATE_BUILDERS, build_all_instances

_GATES = {"G1", "G2", "G3", "G4", "G5"}


def test_every_template_builds_at_least_three_instances():
    for name, builder in TEMPLATE_BUILDERS.items():
        instances = builder()
        assert len(instances) >= 3, f"{name}: only {len(instances)} instances"
        # Each instance declares the template it belongs to.
        assert all(i.template == name for i in instances), name


def test_instance_ids_unique_across_corpus():
    ids = [i.instance_id for i in build_all_instances()]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate instance ids: {sorted(dupes)}"


def test_corpus_covers_all_four_boundaries_plus_benign_and_crosscut():
    boundaries = {i.boundary.split(".")[0] for i in build_all_instances()}
    # B1/B3/B4/B5 active gates, XC cross-cutting, B0 benign FP set.
    for expected in ("B1", "B3", "B4", "B5", "XC", "B0"):
        assert expected in boundaries, f"missing boundary prefix {expected}"


def test_committed_yaml_matches_builders():
    """Every built instance is materialized on disk and loads/validates."""
    built = build_all_instances()
    loaded = load_instances(CORPUS_DIR)  # validates each against the schema
    assert len(loaded) == len(built), (
        f"{len(loaded)} YAML on disk vs {len(built)} built -- "
        "regenerate with `python -m "
        "vista_backend.vistaguard.sciagentbench.corpus_builder`"
    )
    assert {i.instance_id for i in loaded} == {i.instance_id for i in built}


def test_every_template_has_a_corpus_dir():
    for name in TEMPLATE_BUILDERS:
        d = Path(CORPUS_DIR) / name
        assert d.is_dir(), f"no corpus dir for {name}"
        assert len(list(d.glob("*.yaml"))) >= 3, name


def test_actions_route_to_a_real_gate():
    """Every gated action resolves to one of the five real gates."""
    for inst in build_all_instances():
        for session in inst.sessions:
            for turn in session.turns:
                for action in turn.actions:
                    gid = action.resolved_gate()
                    # RESPONSE/MEMORY_WRITE may have no gate; everything
                    # else must resolve to a real gate id.
                    if action.kind in (ActionKind.RESPONSE, ActionKind.MEMORY_WRITE):
                        continue
                    assert gid in _GATES, (
                        f"{inst.instance_id}: {action.kind} -> {gid!r}"
                    )


def test_benign_workload_is_all_benign():
    benign = TEMPLATE_BUILDERS["benign_workload"]()
    assert benign, "benign_workload built no instances"
    assert all(not i.is_attack for i in benign)
    assert all(i.kind == "benign" for i in benign)
