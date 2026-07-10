"""Tests for the campaign.yaml manifest loader."""

import pytest

from vista_backend.agents.campaign.manifest import load_manifest


MANIFEST_YAML = """
domain: testdomain
variables:
  - {name: x, range: [0.0, 1.0], unit: fraction}
  - {name: temp, range: [700, 1000], unit: K}
metrics:
  primary: {name: SCORE, target: 1.1, direction: maximize}
  scorer: scripts/score.py
subagents:
  - {role: alpha, skill: alpha-skill, job: alpha_job, default_count: 1}
  - {role: beta, skill: beta-skill, job: beta_job, default_count: 2}
search:
  strategy: grid
"""


def _write_manifest(skill_dir, text=MANIFEST_YAML):
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "campaign.yaml").write_text(text)
    return skill_dir


def test_load_manifest_parses_all_sections(tmp_path):
    skill_dir = _write_manifest(tmp_path / "planner-skill")
    manifest = load_manifest(skill_dir)

    assert manifest.domain == "testdomain"
    assert manifest.roles == ["alpha", "beta"]
    assert manifest.metrics.primary.name == "SCORE"
    assert manifest.metrics.primary.target == 1.1
    assert manifest.metrics.primary.direction == "maximize"
    assert manifest.metrics.scorer == "scripts/score.py"
    assert manifest.search.strategy == "grid"

    # variables coerce list -> tuple
    x = manifest.variables[0]
    assert x.name == "x"
    assert x.range == (0.0, 1.0)
    assert x.unit == "fraction"


def test_subagent_lookup_and_bindings(tmp_path):
    manifest = load_manifest(_write_manifest(tmp_path / "planner-skill"))
    beta = manifest.subagent("beta")
    assert beta is not None
    assert beta.skill == "beta-skill"
    assert beta.job == "beta_job"
    assert beta.default_count == 2
    assert manifest.subagent("missing") is None


def test_search_defaults_to_bayesian_when_omitted(tmp_path):
    text = """
domain: d
metrics:
  primary: {name: SCORE}
subagents:
  - {role: a, skill: a-skill, job: a_job}
"""
    manifest = load_manifest(_write_manifest(tmp_path / "planner-skill", text))
    assert manifest.search.strategy == "bayesian"
    assert manifest.subagents[0].default_count == 1
    assert manifest.metrics.primary.direction == "maximize"


def test_missing_manifest_raises(tmp_path):
    (tmp_path / "planner-skill").mkdir()
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path / "planner-skill")


def test_non_mapping_manifest_raises(tmp_path):
    skill_dir = _write_manifest(tmp_path / "planner-skill", "- just\n- a\n- list\n")
    with pytest.raises(ValueError):
        load_manifest(skill_dir)
