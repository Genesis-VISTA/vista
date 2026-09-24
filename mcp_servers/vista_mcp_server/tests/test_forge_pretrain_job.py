"""
hpc_jobs/forge-pretrain: the merged-config generator, the Lux job script's
argument handling (run under bash with the cluster commands stubbed out), and
a submission of the real job dir through the Lux dispatcher.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

import vista_mcp_server.submit_job_mcp as m
from vista_mcp_server.config import settings
from fakes import FakeSshConn

REPO_ROOT = Path(__file__).resolve().parents[3]
JOB_DIR = REPO_ROOT / "hpc_jobs" / "forge-pretrain"
DATA = "/lustre/orion/world-shared/stf218/junqi/data/forge"

pytestmark = pytest.mark.unit

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _load_make_config():
    spec = importlib.util.spec_from_file_location(
        "make_config", JOB_DIR / "make_config.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


make_config = _load_make_config()

# Shaped like forge@lux configs: JSON-ish YAML with Python booleans, a relative
# hostfile (forge-l), and keys that the run must override.
MODEL_YML = textwrap.dedent("""\
    {
     "hostfile": "logs/forge-l/hostfile",
     "data-path": "/old/tokens/all_text_document",
     "vocab-file": "/old/tokens/all_vocab.json",
     "model-parallel-size": 2,
     "fp16": {"type": "bfloat16", "enabled": True},
     "train-iters": 50,
     "lr-decay-iters": 15300,
     "checkpoint-factor": 50,
     "log-interval": 1,
    }
""")
LUX_YML = textwrap.dedent("""\
    {
      "save": "checkpoints",
      "load": "checkpoints",
      "tensorboard-dir": "tensorboard",
      "log-dir": "logs",
      "use_wandb": false,
      "launcher": "slurm",
      "deepspeed_slurm": true
    }
""")


def _configs(tmp_path: Path, model: str = MODEL_YML) -> tuple[Path, Path]:
    mp, cp = tmp_path / "forge-l.yml", tmp_path / "lux.yml"
    mp.write_text(model, encoding="utf-8")
    cp.write_text(LUX_YML, encoding="utf-8")
    return mp, cp


def _run(tmp_path: Path, *extra: str) -> dict:
    mp, cp = _configs(tmp_path)
    out = tmp_path / "out"
    rc = make_config.main(
        [
            str(mp),
            str(cp),
            str(out / "config" / "forge-l.yml"),
            "--out-dir",
            str(out),
            "--data-dir",
            DATA,
            *extra,
        ]
    )
    assert rc == 0
    text = (out / "config" / "forge-l.yml").read_text(encoding="utf-8")
    return yaml.load(text, Loader=yaml.FullLoader)


def test_defaults_point_everything_at_the_run_dir(tmp_path):
    cfg = _run(tmp_path)
    out = str(tmp_path / "out")
    assert cfg["hostfile"] == f"{out}/hostfile"
    assert cfg["data-path"] == f"{DATA}/all_text_document"
    assert cfg["vocab-file"] == f"{DATA}/all_vocab.json"
    assert cfg["save"] == cfg["load"] == f"{out}/checkpoints"
    assert cfg["log-dir"] == f"{out}/logs"
    assert cfg["tensorboard-dir"] == f"{out}/tensorboard"
    assert cfg["train-iters"] == 50
    assert cfg["checkpoint-factor"] == 50  # one checkpoint, at the end
    assert cfg["lr-decay-iters"] == 15300  # untouched unless asked
    # Model/cluster settings pass through, booleans intact.
    assert cfg["model-parallel-size"] == 2
    assert cfg["fp16"]["enabled"] is True
    assert cfg["launcher"] == "slurm" and cfg["deepspeed_slurm"] is True


def test_no_key_appears_twice_under_either_spelling(tmp_path):
    # NeoX normalizes '-' to '_' and rejects duplicates.
    cfg = _run(tmp_path, "--log-interval", "5")
    normalized = [k.replace("-", "_") for k in cfg]
    assert len(normalized) == len(set(normalized))
    assert cfg["log-interval"] == 5
    assert cfg["steps_per_print"] == 5


def test_overrides(tmp_path):
    cfg = _run(
        tmp_path,
        "--train-iters",
        "200",
        "--save-interval",
        "100",
        "--lr-decay-iters",
        "200",
        "--load-dir",
        "/prev/run/checkpoints",
    )
    assert cfg["train-iters"] == 200
    assert cfg["checkpoint-factor"] == 100
    assert cfg["lr-decay-iters"] == 200
    assert cfg["load"] == "/prev/run/checkpoints"
    assert cfg["save"] == str(tmp_path / "out" / "checkpoints")


def test_save_interval_zero_disables_checkpointing(tmp_path):
    cfg = _run(tmp_path, "--save-interval", "0")
    assert cfg["save"] is None
    assert cfg["load"] is None


def test_model_and_cluster_configs_may_not_collide(tmp_path):
    mp, cp = _configs(tmp_path, model='{"save": "elsewhere", "train_iters": 5}')
    with pytest.raises(SystemExit, match="save"):
        make_config.main(
            [
                str(mp),
                str(cp),
                str(tmp_path / "o.yml"),
                "--out-dir",
                "o",
                "--data-dir",
                "d",
            ]
        )


# ------------------------------------------------------------------ job.lux.slurm


@needs_bash
@pytest.mark.parametrize("script", ["job.lux.slurm", "setup_lux.sh"])
def test_shell_scripts_parse(script):
    subprocess.run(["bash", "-n", str(JOB_DIR / script)], check=True)


def _stub(bin_dir: Path, name: str, body: str) -> None:
    p = bin_dir / name
    p.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    p.chmod(0o755)


@pytest.fixture
def job_env(tmp_path):
    """Run job.lux.slurm with scontrol/python/git/rocm-smi stubbed, recording calls."""
    bin_dir = tmp_path / "bin"
    rocm = tmp_path / "rocm"
    (rocm / "bin").mkdir(parents=True)
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    _stub(bin_dir, "scontrol", 'printf "lux001\\nlux002\\n"')
    _stub(bin_dir, "python", f'echo "python $*" >> {calls}')
    _stub(bin_dir, "git", 'echo "abc1234 latest"')
    _stub(rocm / "bin", "rocm-smi", "true")
    env_script = tmp_path / "lux_env.sh"
    env_script.write_text(f'export ROCM_PATH="{rocm}"\n', encoding="utf-8")
    out = tmp_path / "vista_out"
    out.mkdir()
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "VISTA_OUT": str(out),
        "VISTA_JOB_DIR": "/lustre/vista/forge-pretrain",
        "RUN_DIR_Lux": "/lustre/vista/forge-pretrain/src",
        "FORGE_DATA_DIR": DATA,
        "LUX_ENV_SCRIPT": str(env_script),
        "SLURM_JOB_ID": "4242",
        "SLURM_NNODES": "2",
        "SLURM_NODELIST": "lux[001-002]",
    }

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(JOB_DIR / "job.lux.slurm"), *args],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def recorded() -> list[str]:
        return calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []

    return run, recorded, out


@needs_bash
def test_job_defaults(job_env):
    run, recorded, out = job_env
    r = run()
    assert r.returncode == 0, r.stderr
    make, train = recorded()
    train_dir = "/lustre/vista/forge-pretrain/forge/train"
    assert make.startswith("python -u /lustre/vista/forge-pretrain/src/make_config.py ")
    # Defaults: forge-l, no checkpoint.
    assert f"{train_dir}/configs/forge-l.yml {train_dir}/configs/lux.yml" in make
    assert (
        f"--out-dir {out} --data-dir /lustre/vista/forge-pretrain/data"
        " --train-iters 50 --log-interval 1"
        " --save-interval 0" in make
    )
    assert "--load-dir" not in make
    assert train == (
        f"python -u {train_dir}/deepy.py {train_dir}/train.py {out}/config/forge-l.yml"
    )
    assert (out / "hostfile").read_text(encoding="utf-8") == (
        "lux001 slots=8\nlux002 slots=8\n"
    )
    assert (out / ".deepspeed_env").read_text(encoding="utf-8").startswith("PATH=")


@needs_bash
def test_job_script_args(job_env):
    run, recorded, out = job_env
    r = run(
        "MODEL=forge-m",
        "TRAIN_ITERS=100",
        "SAVE_INTERVAL=100",
        "LOAD_DIR=/prev/ckpt",
    )
    assert r.returncode == 0, r.stderr
    make, train = recorded()
    assert "configs/forge-m.yml" in make
    assert "--train-iters 100" in make
    assert "--save-interval 100" in make
    assert "--load-dir /prev/ckpt" in make
    assert train.endswith(f"{out}/config/forge-m.yml")


@needs_bash
@pytest.mark.parametrize(
    ("arg", "message"),
    [("MODEL=forge-xl", "MODEL must be"), ("EPOCHS=3", "unknown script arg")],
)
def test_job_rejects_bad_args(job_env, arg, message):
    run, recorded, _ = job_env
    r = run(arg)
    assert r.returncode == 2
    assert message in r.stderr
    assert recorded() == []


# ------------------------------------------------------------------ setup_lux.sh


@pytest.fixture
def setup_env(tmp_path):
    """Run setup_lux.sh with a stub git that records calls and fakes a clone."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "git.log"
    # `git clone ... <url> <dest>`: materialize a checkout at <dest>.
    _stub(
        bin_dir,
        "git",
        f"""echo "git $*" >> {calls}
if [ "$1" = clone ]; then
  dest="${{@: -1}}"; mkdir -p "$dest/.git" "$dest/train/configs"
  touch "$dest/train/deepy.py" "$dest/train/train.py" "$dest/train/configs/lux.yml"
fi
case "$*" in *"log -1"*) echo "abc1234 2026-09-14 add lux config";; esac""",
    )
    data = tmp_path / "data"
    data.mkdir()
    for f in ("all_text_document.bin", "all_text_document.idx", "all_vocab.json"):
        (data / f).write_text("x", encoding="utf-8")
    env_script = tmp_path / "lux_env.sh"
    env_script.write_text("true\n", encoding="utf-8")
    job_dir = tmp_path / "forge-pretrain"
    job_dir.mkdir()
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "VISTA_JOB_DIR": str(job_dir),
        "FORGE_REPO_URL": "https://github.com/at-aaims/forge.git",
        "FORGE_BRANCH": "lux",
        "FORGE_DATA_DIR": str(data),
        "LUX_ENV_SCRIPT": str(env_script),
    }

    def run() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(JOB_DIR / "setup_lux.sh")],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def git_calls() -> list[str]:
        return calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []

    return run, git_calls, job_dir, data


@needs_bash
def test_setup_clones_then_updates_to_latest(setup_env):
    run, git_calls, job_dir, data = setup_env
    r = run()
    assert r.returncode == 0, r.stderr
    assert any(
        "clone -q --depth 1 --branch lux" in c and c.endswith(f"{job_dir}/forge.tmp")
        for c in git_calls()
    )
    assert (job_dir / "forge" / ".git").is_dir()
    assert not (job_dir / "forge.tmp").exists()
    assert (job_dir / "torch_extensions").is_dir()
    assert "abc1234" in r.stdout and "data and env OK" in r.stdout
    # NeoX writes index maps next to the data prefix: it must be a writable dir
    # of links, not the read-only corpus dir.
    links = job_dir / "data"
    for f in ("all_text_document.bin", "all_text_document.idx", "all_vocab.json"):
        assert (links / f).is_symlink()
        assert (links / f).resolve() == (data / f).resolve()

    before = len(git_calls())
    r = run()
    assert r.returncode == 0, r.stderr
    later = git_calls()[before:]
    assert not any(c.startswith("git clone ") for c in later)
    assert any("fetch -q --depth 1 origin lux" in c for c in later)
    assert any("checkout -q -f -B lux FETCH_HEAD" in c for c in later)
    # Another user's checkout: git must be told it is safe.
    assert all(f"safe.directory={job_dir}/forge" in c for c in later)


@needs_bash
def test_setup_fails_on_missing_data(setup_env):
    run, _, _, data = setup_env
    (data / "all_vocab.json").unlink()
    r = run()
    assert r.returncode == 1
    assert "all_vocab.json not readable" in r.stderr


# ------------------------------------------------------------------ dispatch


@pytest.mark.anyio
async def test_real_job_dir_submits_on_lux(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", JOB_DIR.parent)
    monkeypatch.setattr(m, "AVAILABLE_JOBS", m.get_available_jobs())
    monkeypatch.setattr(settings, "lux_remote_dir", "/lustre/vista")
    monkeypatch.setattr(settings, "lux_account", "csc708")
    monkeypatch.setattr(settings, "session_id", "sess")
    conn = FakeSshConn(tmp_path)
    conn.on("sbatch --parsable", (0, "99\n", ""))

    async def fake_conn(ctx, tool, **kw):
        return conn

    monkeypatch.setattr(m, "_lux_conn", fake_conn)

    job_id, _, out_dir, nodes, duration = await m._submit_lux_job(
        None, "forge-pretrain", None, None, "MODEL=forge-m"
    )
    assert (job_id, nodes, duration) == ("99", 16, 1800)
    assert out_dir == "/lustre/vista/sess/out/99"
    assert conn.puts == ["/lustre/vista/forge-pretrain/src/make_config.py"]

    [(setup, _)] = conn.ran("git clone")
    assert "export FORGE_BRANCH=lux" in setup
    assert "export VISTA_JOB_DIR=/lustre/vista/forge-pretrain" in setup

    [(_, script)] = conn.ran("sbatch --parsable")
    assert "#SBATCH -A csc708" in script
    assert "#SBATCH -N 16" in script
    assert "#SBATCH -t 0:30:00" in script
    assert f"export FORGE_DATA_DIR={DATA}" in script
    assert "set -- MODEL=forge-m" in script
    assert "deepy.py" in script
