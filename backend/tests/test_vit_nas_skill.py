"""Validate the vit-nas-planner skill: its campaign.yaml manifest + the efficiency scorer."""
import importlib.util
from pathlib import Path

import vista_backend
from vista_backend.agents.campaign.manifest import load_manifest


SKILL_DIR = Path(vista_backend.__file__).parent / "db" / "skills" / "vit-nas-planner"


def _load_scorer():
    path = SKILL_DIR / "scripts" / "score_candidates.py"
    spec = importlib.util.spec_from_file_location("vit_nas_scorer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- manifest --------------------------------------------------------------

def test_campaign_manifest_is_valid():
    manifest = load_manifest(SKILL_DIR)
    assert manifest.domain == "vit-nas"
    assert manifest.roles == ["training"]
    assert manifest.metrics.primary.name == "efficiency"
    assert manifest.metrics.primary.target == 185
    assert manifest.metrics.primary.direction == "maximize"
    assert manifest.metrics.scorer == "scripts/score_candidates.py"
    assert {v.name for v in manifest.variables} == {
        "embed_dim", "depth", "num_heads", "patch_size",
        "lr", "global_batch_size", "tensor_parallel", "context_parallel",
    }
    # Single training role bound to the vit-train sim skill + HPC job.
    training = manifest.subagent("training")
    assert training.skill == "vit-train" and training.job == "vit-train"
    assert training.default_count == 1


# --- scorer ----------------------------------------------------------------

def _cand(*, val_loss, throughput, **params):
    metrics = {}
    if val_loss is not None:
        metrics["val_loss"] = val_loss
    if throughput is not None:
        metrics["throughput_samples_s"] = throughput
    return {"params": params or {"embed_dim": 1024}, "metrics": metrics}


def test_scorer_ranks_feasible_by_efficiency_and_reports_target():
    scorer = _load_scorer()
    result = scorer.score_candidates(
        [
            _cand(val_loss=0.42, throughput=85.0, embed_dim=1024),   # 202.4
            _cand(val_loss=0.60, throughput=100.0, embed_dim=2048),  # 166.7
            _cand(val_loss=0.50, throughput=40.0, embed_dim=4096),   # 80.0
        ],
        efficiency_target=185,
    )
    effs = [round(e["efficiency"], 1) for e in result["ranked"]]
    assert effs == [202.4, 166.7, 80.0]
    assert result["best"]["params"]["embed_dim"] == 1024
    assert result["target_met"] is True
    assert result["infeasible"] == []


def test_scorer_target_not_met_when_best_below_target():
    scorer = _load_scorer()
    result = scorer.score_candidates([_cand(val_loss=0.5, throughput=40.0)], efficiency_target=185)
    assert result["target_met"] is False
    assert round(result["best"]["efficiency"], 1) == 80.0


def test_scorer_marks_missing_or_invalid_metrics_infeasible():
    scorer = _load_scorer()
    result = scorer.score_candidates(
        [
            _cand(val_loss=None, throughput=80.0, embed_dim=768),  # missing val_loss
            _cand(val_loss=0.4, throughput=None, embed_dim=384),   # missing throughput
            _cand(val_loss=0.0, throughput=50.0, embed_dim=512),   # non-positive val_loss
            _cand(val_loss=0.42, throughput=85.0, embed_dim=1024), # ok
        ],
        efficiency_target=185,
    )
    assert result["best"]["params"]["embed_dim"] == 1024
    assert len(result["ranked"]) == 1
    assert len(result["infeasible"]) == 3
    reasons = " ".join(r for cand in result["infeasible"] for r in cand["reasons"])
    assert "val_loss missing" in reasons
    assert "throughput_samples_s missing" in reasons
    assert "not positive" in reasons
