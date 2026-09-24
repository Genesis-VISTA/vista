"""Validate the llm-pretraining skill: SKILL.md parses, and its log parser / plotter."""

import importlib.util
import json
from pathlib import Path

import pytest

import vista_backend
from vista_backend.agents.skills import read_skill

SKILL_DIR = Path(vista_backend.__file__).parent / "db" / "skills" / "llm-pretraining"

pytestmark = pytest.mark.unit


def _load_plotter():
    path = SKILL_DIR / "scripts" / "plot_training.py"
    spec = importlib.util.spec_from_file_location("plot_training", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plotter = _load_plotter()


def _train_line(it, total, loss, ms, flops="150.3TFLOPS", sps=51.2, skipped=0, nan=0):
    return (
        f" samples/sec: {sps:.3f} | iteration {it:8d}/{total:8d} |"
        f" elapsed time per iteration (ms): {ms:.1f} | learning rate: 1.234E-04 |"
        f" approx flops per GPU: {flops} | lm_loss: {loss:.6E} |"
        f" number of skipped iterations: {skipped:3d} |"
        f" number of nan iterations: {nan:3d} |"
    )


LOG = "\n".join(
    [
        "[forge-pretrain] job 4242 on 16 node(s), model forge-l",
        "NeoXArgs.from_ymls() ['/lustre/vista/sess/out/4242/config/forge-l.yml']",
        " > building train, validation, and test datasets ...",
        _train_line(1, 50, 11.0, 60000.0, flops="40.1TFLOPS"),
        _train_line(2, 50, 10.5, 20000.0),
        "[2026-09-24 10:00:00] some other rank-0 chatter | iteration noise",
        _train_line(3, 50, 10.1, 21000.0, flops="0.2PFLOPS", skipped=1),
        " validation results at iteration 3 | lm_loss value: 1.020000E+01 |"
        " lm_loss_ppl value: 2.690000E+04 | ",
        _train_line(3, 50, 10.0, 22000.0),  # re-logged (resume): last one wins
    ]
)


def test_skill_md_parses_and_matches_dir():
    skill = read_skill(SKILL_DIR)
    assert skill.name == "llm-pretraining"
    assert "Lux" in skill.description
    body = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert 'job="forge-pretrain"' in body
    assert 'cluster="lux"' in body
    assert "/mnt/skills/llm-pretraining/scripts/plot_training.py" in body


def test_parse_log():
    parsed = plotter.parse_log(LOG)
    assert parsed["total_iters"] == 50
    rows = parsed["train"]
    assert [r["iteration"] for r in rows] == [1, 2, 3]
    assert rows[0]["lm_loss"] == pytest.approx(11.0)
    assert rows[0]["tflops_per_gpu"] == pytest.approx(40.1)
    assert rows[1]["ms_per_iter"] == pytest.approx(20000.0)
    assert rows[1]["samples_per_sec"] == pytest.approx(51.2)
    assert rows[1]["learning_rate"] == pytest.approx(1.234e-4)
    assert rows[2]["lm_loss"] == pytest.approx(10.0)  # the re-logged line
    assert parsed["validation"] == [{"iteration": 3, "lm_loss": pytest.approx(10.2)}]


def test_units_are_normalized_to_tflops():
    rows = plotter.parse_log(
        "\n".join(
            [
                _train_line(1, 9, 1.0, 1.0, flops="950.0GFLOPS"),
                _train_line(2, 9, 1.0, 1.0, flops="1.2PFLOPS"),
            ]
        )
    )["train"]
    assert rows[0]["tflops_per_gpu"] == pytest.approx(0.95)
    assert rows[1]["tflops_per_gpu"] == pytest.approx(1200.0)


def test_summary_skips_warmup_and_estimates_eta():
    s = plotter.summarize(plotter.parse_log(LOG))
    assert s["last_iteration"] == 3
    assert s["first_lm_loss"] == pytest.approx(11.0)
    assert s["last_lm_loss"] == pytest.approx(10.0)
    # Iteration 1 (60 s, warm-up) is excluded from the medians.
    assert s["median_ms_per_iter"] == pytest.approx(21000.0)
    assert s["median_tflops_per_gpu"] == pytest.approx(150.3)
    assert s["eta_seconds"] == round(47 * 21.0)
    assert s["last_validation"]["iteration"] == 3


def test_empty_log_summarizes_without_plotting(tmp_path, capsys):
    log = tmp_path / "log.out"
    log.write_text("[forge-pretrain] job 1 on 16 node(s)\n", encoding="utf-8")
    png = tmp_path / "p.png"
    assert plotter.main(["--log", str(log), "--output", str(png)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["iterations_logged"] == 0
    assert summary["plot"] is None
    assert not png.exists()
    assert (tmp_path / "p.csv").read_text(encoding="utf-8").startswith("iteration,")


def test_missing_log_is_reported(tmp_path, capsys):
    rc = plotter.main(
        ["--log", str(tmp_path / "nope"), "--output", str(tmp_path / "p.png")]
    )
    assert rc == 1
    assert "log not found" in json.loads(capsys.readouterr().out)["error"]


def test_plot_and_csv(tmp_path, capsys):
    pytest.importorskip("matplotlib")
    log = tmp_path / "log.out"
    log.write_text(LOG, encoding="utf-8")
    png = tmp_path / "out" / "training_progress.png"
    assert plotter.main(["--log", str(log), "--output", str(png)]) == 0
    assert png.stat().st_size > 0
    lines = (
        (tmp_path / "out" / "training_progress.csv")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert lines[0].startswith("iteration,lm_loss")
    assert len(lines) == 4
    assert json.loads(capsys.readouterr().out)["plot"] == str(png)
