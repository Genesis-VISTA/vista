"""The vit-train job wrapper: render a ViT.yaml from a candidate + parse training metrics.

run_state_point.py turns one campaign candidate (the JSON wire format) into a climate-vit
config block + an srun train_mp.py launch, then scrapes val_loss / throughput from the log.
The wrapper lives outside the backend package, so load it by path. We test the pure helpers
(config render, metric parse, geometry, launch command) without an HPC allocation.
"""

import importlib.util
from pathlib import Path

import pytest
import vista_backend
from vista_backend.agents.skills import read_skill

_WRAPPER = (
    Path(__file__).resolve().parents[2]
    / "hpc_jobs"
    / "vit-train"
    / "run_state_point.py"
)

SAMPLE_LOG = """\
2024-11-08 22:23:44 - root - INFO - Time taken for epoch 1 is 61.4 sec, avg 8.33 samples/sec
2024-11-08 22:23:44 - root - INFO -   Avg train loss=0.5777
2024-11-08 22:23:49 - root - INFO -   Avg val loss=0.4210963547229767
2024-11-08 22:25:10 - root - INFO - Time taken for epoch 2 is 60.1 sec, avg 9.12 samples/sec
2024-11-08 22:25:10 - root - INFO -   Avg train loss=0.4011
2024-11-08 22:25:15 - root - INFO -   Avg val loss=0.331
"""


def _load_wrapper():
    spec = importlib.util.spec_from_file_location("vit_train_run_state_point", _WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- render_config ---------------------------------------------------------


def test_render_config_overrides_search_fields_and_sets_run_dir():
    mod = _load_wrapper()
    cfg = mod.render_config(
        {
            "embed_dim": 1024,
            "depth": 16,
            "num_heads": 8,
            "patch_size": 4,
            "lr": 1e-3,
            "global_batch_size": 32,
            "tensor_parallel": 2,
            "context_parallel": 1,
        },
        expdir="/out/expdir",
        num_iters=500,
        data_root="/data/era5",
    )
    assert cfg["embed_dim"] == 1024 and cfg["depth"] == 16 and cfg["num_heads"] == 8
    assert (
        cfg["patch_size"] == 4 and cfg["lr"] == 1e-3 and cfg["global_batch_size"] == 32
    )
    assert cfg["expdir"] == "/out/expdir"
    assert cfg["num_iters"] == 500
    assert cfg["train_data_path"] == "/data/era5/train"
    assert cfg["global_stds_path"] == "/data/era5/stats/global_stds.npy"
    # Parallelism keys are launcher flags, not config fields.
    assert "tensor_parallel" not in cfg


def test_render_config_rejects_indivisible_heads():
    mod = _load_wrapper()
    with pytest.raises(ValueError):
        mod.render_config({"embed_dim": 1000, "num_heads": 7}, expdir="/out")


# --- parse_metrics ---------------------------------------------------------


def test_parse_metrics_takes_last_val_loss_and_throughput():
    mod = _load_wrapper()
    metrics = mod.parse_metrics(SAMPLE_LOG)
    assert metrics["val_loss"] == 0.331  # last "Avg val loss=", not the train loss
    assert metrics["throughput_samples_s"] == 9.12  # last "avg N samples/sec"


def test_parse_metrics_none_when_absent():
    mod = _load_wrapper()
    assert mod.parse_metrics("nothing useful here") == {
        "val_loss": None,
        "throughput_samples_s": None,
    }


# --- geometry + launch -----------------------------------------------------


def test_compute_ntasks_requires_tp_cp_to_divide_gpus():
    mod = _load_wrapper()
    assert mod.compute_ntasks(2, 1, nnodes=1, gpus_per_node=8) == 8
    assert mod.compute_ntasks(2, 4, nnodes=2, gpus_per_node=8) == 16
    with pytest.raises(ValueError):
        mod.compute_ntasks(3, 1, nnodes=1, gpus_per_node=8)  # 3 does not divide 8


def test_build_train_cmd_has_parallelism_flags_and_config():
    mod = _load_wrapper()
    cmd = mod.build_train_cmd(
        skill_root="/clone",
        yaml_path="/out/ViT.nas.yaml",
        config_name="nas",
        tp=2,
        cp=1,
        nnodes=1,
        ntasks=8,
    )
    assert cmd[0] == "srun"
    inner = cmd[-1]
    assert "train_mp.py" in inner
    assert "--tensor_parallel=2" in inner and "--context_parallel=1" in inner
    assert "--config nas" in inner
    assert "ViT.nas.yaml" in inner


# --- dry-run main (renders the config, no training) ------------------------


def test_main_dry_run_writes_config(tmp_path):
    mod = _load_wrapper()
    out = tmp_path / "out"
    rc = mod.main(
        [
            "--skill-root",
            str(tmp_path / "clone"),
            "--output-dir",
            str(out),
            "--dry-run",
            '{"embed_dim": 768, "num_heads": 8, "depth": 12, "tensor_parallel": 1, "context_parallel": 1}',
        ]
    )
    assert rc == 0
    import yaml

    cfg = yaml.safe_load((out / "ViT.nas.yaml").read_text())
    assert cfg["nas"]["embed_dim"] == 768
    assert cfg["nas"]["expdir"] == str(out / "expdir")


# --- skill frontmatter -----------------------------------------------------


def test_vit_train_skill_loads():
    skill_dir = Path(vista_backend.__file__).parent / "db" / "skills" / "vit-train"
    skill = read_skill(skill_dir)
    assert skill.name == "vit-train"
    assert "val_loss" in skill.body and "throughput_samples_s" in skill.body
