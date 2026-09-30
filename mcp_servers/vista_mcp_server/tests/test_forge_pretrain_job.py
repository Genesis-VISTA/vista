"""
hpc_jobs/forge-pretrain: the merged-config generator, the Lux job script's
argument handling (run under bash with the cluster commands stubbed out), and
a submission of the real job dir through the Lux dispatcher.
"""

from __future__ import annotations

import importlib.util
import json
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


def test_output_passes_deeperspeeds_slurm_launcher_check(tmp_path):
    """
    NeoX passes the raw text of each config file to the launcher, and
    DeeperSpeed's SlurmRunner.parse_user_args `json.loads` every one. YAML output
    failed there with `JSONDecodeError: Expecting value` on the first Lux run.
    """
    _run(tmp_path)
    text = (tmp_path / "out" / "config" / "forge-l.yml").read_text(encoding="utf-8")
    # What DeeperSpeed does with the megatron_config argument:
    arg = json.dumps({"config_files": {"forge-l.yml": text}})
    for v in json.loads(arg)["config_files"].values():
        json.loads(v)


def test_output_means_the_same_to_json_and_pyyaml(tmp_path):
    # NeoX reads the file with PyYAML; the launcher with json. PyYAML reads a
    # float without a dot in its mantissa (Python's "1e-08") as a string.
    model = MODEL_YML.replace(
        '"log-interval": 1,', '"log-interval": 1, "min_lr": 1.0e-05, "eps": 1.0e-08,'
    )
    mp, cp = _configs(tmp_path, model=model)
    out = tmp_path / "o" / "c.yml"
    make_config.main([str(mp), str(cp), str(out), "--out-dir", "o", "--data-dir", "d"])
    text = out.read_text(encoding="utf-8")
    as_json = json.loads(text)
    as_yaml = yaml.load(text, Loader=yaml.FullLoader)
    assert as_json == as_yaml
    assert as_yaml["min_lr"] == 1e-05 and isinstance(as_yaml["min_lr"], float)
    assert as_yaml["eps"] == 1e-08 and isinstance(as_yaml["eps"], float)
    assert as_yaml["fp16"]["enabled"] is True


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (1e-08, "1.0e-08"),
        (1e-05, "1.0e-05"),
        (0.008, "0.008"),
        (2.5e20, "2.5e+20"),
        (1.0, "1.0"),
        (1e16, "1.0e+16"),
    ],
)
def test_float_text(value, text):
    assert make_config._float_text(value) == text
    assert yaml.load(text, Loader=yaml.FullLoader) == value
    assert json.loads(text) == value


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_json_floats_are_refused(bad):
    with pytest.raises(ValueError):
        make_config._float_text(bad)


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


# ------------------------------------------------------------------ shell scripts
#
# The job scripts run under bash with the cluster stubbed out: `git` fakes a
# clone and records calls, `scontrol` lists two nodes, `python` records its
# arguments, `module` and `rocm-smi` do nothing. "Remote" paths are temp dirs;
# RUN_DIR_* is this repo's job dir, which holds exactly what vista uploads.

SHELL_SCRIPTS = [
    "job.lux.slurm",
    "job.frontier.slurm",
    "setup_lux.sh",
    "prepare_forge.sh",
    "forge_common.sh",
]


@needs_bash
@pytest.mark.parametrize("script", SHELL_SCRIPTS)
def test_shell_scripts_parse(script):
    subprocess.run(["bash", "-n", str(JOB_DIR / script)], check=True)


def _stub(bin_dir: Path, name: str, body: str) -> None:
    p = bin_dir / name
    p.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
    p.chmod(0o755)


class Cluster:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        rocm = tmp_path / "rocm"
        (rocm / "bin").mkdir(parents=True)
        self.calls = tmp_path / "calls.log"
        # `git clone ... <url> <dest>`: materialize a checkout at <dest>.
        _stub(
            bin_dir,
            "git",
            f"""echo "git $*" >> {self.calls}
if [ "$1" = clone ]; then
  dest="${{@: -1}}"; mkdir -p "$dest/.git" "$dest/train/configs"
  touch "$dest/train/deepy.py" "$dest/train/train.py"
  touch "$dest/train/configs/lux.yml"
fi
case "$*" in *"log -1"*) echo "abc1234 2026-09-14 add lux config";; esac""",
        )
        _stub(bin_dir, "scontrol", 'printf "node001\\nnode002\\n"')
        self.ntasks = tmp_path / "ntasks.log"
        _stub(
            bin_dir,
            "python",
            f'echo "python $*" >> {self.calls}; echo "${{SLURM_NTASKS:-unset}}" >> {self.ntasks}',
        )
        _stub(bin_dir, "module", f'echo "module $*" >> {self.calls}')
        _stub(rocm / "bin", "rocm-smi", "true")

        self.data = tmp_path / "data"
        self.data.mkdir()
        for f in ("all_text_document.bin", "all_text_document.idx", "all_vocab.json"):
            (self.data / f).write_text("x", encoding="utf-8")
        lux_env = tmp_path / "lux_env.sh"
        lux_env.write_text(f'export ROCM_PATH="{rocm}"\n', encoding="utf-8")
        self.job_dir = tmp_path / "remote" / "forge-pretrain"
        self.job_dir.mkdir(parents=True)
        self.out = tmp_path / "remote" / "sess" / "out" / "4242"
        self.out.mkdir(parents=True)
        self.env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "ROCM_PATH": str(rocm),  # what `module load xforge` would set
            "VISTA_OUT": str(self.out),
            "VISTA_JOB_DIR": str(self.job_dir),
            "RUN_DIR_Lux": str(JOB_DIR),
            "RUN_DIR_Frontier": str(JOB_DIR),
            "FORGE_REPO_URL": "https://github.com/at-aaims/forge.git",
            "FORGE_BRANCH": "lux",
            "FORGE_DATA_DIR": str(self.data),
            "LUX_ENV_SCRIPT": str(lux_env),
            "SLURM_JOB_ID": "4242",
            "SLURM_NNODES": "2",
            "SLURM_NODELIST": "node[001-002]",
        }

    def run(self, script: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(JOB_DIR / script), *args],
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def recorded(self, prefix: str = "") -> list[str]:
        if not self.calls.exists():
            return []
        lines = self.calls.read_text(encoding="utf-8").splitlines()
        return [line for line in lines if line.startswith(prefix)]

    @property
    def train_dir(self) -> str:
        return f"{self.job_dir}/forge/train"


@pytest.fixture
def cluster(tmp_path) -> Cluster:
    return Cluster(tmp_path)


@needs_bash
def test_lux_job_defaults(cluster):
    r = cluster.run("job.lux.slurm")
    assert r.returncode == 0, r.stderr
    make, train = cluster.recorded("python")
    td, out = cluster.train_dir, cluster.out
    assert make.startswith(f"python -u {JOB_DIR}/make_config.py ")
    # Defaults: forge-l, no checkpoint.
    assert f"{td}/configs/forge-l.yml {td}/configs/lux.yml" in make
    assert (
        f"--out-dir {out} --data-dir {cluster.job_dir}/data"
        " --train-iters 50 --log-interval 1 --save-interval 0" in make
    )
    assert "--load-dir" not in make
    assert train == f"python -u {td}/deepy.py {td}/train.py {out}/config/forge-l.yml"
    # NeoX's deepspeed_slurm sets WORLD_SIZE from SLURM_NTASKS in each rank, and
    # the launcher's srun --export=ALL hands the ranks this environment.
    assert cluster.ntasks.read_text(encoding="utf-8").splitlines()[-1] == "16"
    assert (out / "hostfile").read_text(encoding="utf-8") == (
        "node001 slots=8\nnode002 slots=8\n"
    )
    assert (out / ".deepspeed_env").read_text(encoding="utf-8").startswith("PATH=")
    assert (cluster.job_dir / "torch_extensions" / "lux").is_dir()
    # The checkout was updated on the login node already, not here.
    assert cluster.recorded("git clone") == []


@needs_bash
def test_lux_job_script_args(cluster):
    r = cluster.run(
        "job.lux.slurm",
        "MODEL=forge-m",
        "TRAIN_ITERS=100",
        "SAVE_INTERVAL=100",
        "LOAD_DIR=/prev/ckpt",
    )
    assert r.returncode == 0, r.stderr
    make, train = cluster.recorded("python")
    assert "configs/forge-m.yml" in make
    assert "--train-iters 100" in make
    assert "--save-interval 100" in make
    assert "--load-dir /prev/ckpt" in make
    assert train.endswith(f"{cluster.out}/config/forge-m.yml")


@needs_bash
@pytest.mark.parametrize("script", ["job.lux.slurm", "job.frontier.slurm"])
@pytest.mark.parametrize(
    ("arg", "message"),
    [("MODEL=forge-xl", "MODEL must be"), ("EPOCHS=3", "unknown script arg")],
)
def test_jobs_reject_bad_args(cluster, script, arg, message):
    r = cluster.run(script, arg)
    assert r.returncode == 2
    assert message in r.stderr
    assert cluster.recorded("python") == []
    assert cluster.recorded("git clone") == []  # rejected before touching anything


@needs_bash
def test_frontier_job_updates_checkout_then_trains_with_xforge(cluster):
    # IRI's allocation: one task per node.
    cluster.env.update({"SLURM_NTASKS": "2", "SLURM_NTASKS_PER_NODE": "1"})
    _stub(
        Path(cluster.env["PATH"].split(":")[0]),
        "python",
        f'echo "python $*" >> {cluster.calls}; '
        f'echo "${{SLURM_NTASKS:-unset}}" >> {cluster.ntasks}; '
        f'echo "${{SLURM_NTASKS_PER_NODE:-unset}}" >> {cluster.tmp / "ppn.log"}',
    )
    r = cluster.run("job.frontier.slurm", "MODEL=forge-s")
    assert r.returncode == 0, r.stderr
    calls = cluster.recorded()
    clone = next(i for i, c in enumerate(calls) if c.startswith("git clone "))
    load = calls.index("module load xforge")
    make = next(i for i, c in enumerate(calls) if "make_config.py" in c)
    assert clone < load < make  # checkout first (no login-node step on IRI)
    assert "module use /sw/aaims/crusher/modulefiles" in calls
    td = cluster.train_dir
    # Same launch as Lux: lux.yml (deepspeed_slurm), not frontier.yml (MPI).
    assert f"{td}/configs/forge-s.yml {td}/configs/lux.yml" in calls[make]
    # IRI's one-task-per-node allocation must not leak into DeepSpeed's srun:
    # the ranks' world size is the full N x 8.
    assert cluster.ntasks.read_text(encoding="utf-8").splitlines()[-1] == "16"
    ds_env = (cluster.out / ".deepspeed_env").read_text(encoding="utf-8")
    assert "NCCL_SOCKET_IFNAME=hsn" in ds_env
    assert "FI_CXI_ATS=0" in ds_env
    assert f"TORCH_EXTENSIONS_DIR={cluster.job_dir}/torch_extensions/frontier" in ds_env
    assert (cluster.job_dir / "data" / "all_vocab.json").is_symlink()
    ppn = (cluster.tmp / "ppn.log").read_text(encoding="utf-8").splitlines()
    assert ppn[-1] == "unset"


@needs_bash
@pytest.mark.parametrize(
    ("default", "args", "model"),
    [
        (None, [], "forge-l"),  # unset: forge-l
        ("forge-s", [], "forge-s"),  # the cluster's default (Frontier)
        ("forge-s", ["MODEL=forge-m"], "forge-m"),  # the user's choice wins
    ],
)
def test_default_model_comes_from_the_cluster(cluster, default, args, model):
    if default:
        cluster.env["FORGE_DEFAULT_MODEL"] = default
    r = cluster.run("job.lux.slurm", *args)
    assert r.returncode == 0, r.stderr
    make, train = cluster.recorded("python")
    assert f"configs/{model}.yml" in make
    assert train.endswith(f"config/{model}.yml")


@needs_bash
def test_bad_cluster_default_model_is_rejected(cluster):
    cluster.env["FORGE_DEFAULT_MODEL"] = "forge-xl"
    r = cluster.run("job.lux.slurm")
    assert r.returncode == 2
    assert "MODEL must be" in r.stderr


def test_cluster_default_models():
    defaults = json.loads(
        (JOB_DIR / "cluster_defaults.json").read_text(encoding="utf-8")
    )
    # Frontier's GPUs have less memory than Lux's.
    assert defaults["lux"]["iri"]["environment"]["FORGE_DEFAULT_MODEL"] == "forge-l"
    assert (
        defaults["frontier"]["iri"]["environment"]["FORGE_DEFAULT_MODEL"] == "forge-s"
    )


@needs_bash
def test_setup_lux_clones_then_updates_to_latest(cluster):
    r = cluster.run("setup_lux.sh")
    assert r.returncode == 0, r.stderr
    job_dir = cluster.job_dir
    assert any(
        "clone -q --depth 1 --branch lux" in c and c.endswith(f"{job_dir}/forge.tmp")
        for c in cluster.recorded("git clone")
    )
    assert (job_dir / "forge" / ".git").is_dir()
    assert not (job_dir / "forge.tmp").exists()
    assert (job_dir / "torch_extensions").is_dir()
    assert "abc1234" in r.stdout and "data OK" in r.stdout
    # NeoX writes index maps next to the data prefix: it must be a writable dir
    # of links, not the read-only corpus dir.
    links = job_dir / "data"
    for f in ("all_text_document.bin", "all_text_document.idx", "all_vocab.json"):
        assert (links / f).is_symlink()
        assert (links / f).resolve() == (cluster.data / f).resolve()

    before = len(cluster.recorded("git"))
    r = cluster.run("setup_lux.sh")
    assert r.returncode == 0, r.stderr
    later = cluster.recorded("git")[before:]
    assert not any(c.startswith("git clone ") for c in later)
    assert any("fetch -q --depth 1 origin lux" in c for c in later)
    assert any("checkout -q -f -B lux FETCH_HEAD" in c for c in later)
    # Another user's checkout: git must be told it is safe.
    assert all(f"safe.directory={job_dir}/forge" in c for c in later)


@needs_bash
def test_setup_lux_fails_on_missing_data(cluster):
    (cluster.data / "all_vocab.json").unlink()
    r = cluster.run("setup_lux.sh")
    assert r.returncode == 1
    assert "all_vocab.json not readable" in r.stderr


@needs_bash
def test_setup_lux_fails_on_missing_env_script(cluster):
    cluster.env["LUX_ENV_SCRIPT"] = str(cluster.tmp / "nope.sh")
    r = cluster.run("setup_lux.sh")
    assert r.returncode == 1
    assert "nope.sh not readable" in r.stderr
    assert cluster.recorded("git") == []


# ------------------------------------------------------------------ dispatch


@pytest.mark.anyio
async def test_real_job_dir_submits_on_lux(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", JOB_DIR.parent)
    monkeypatch.setattr(m, "AVAILABLE_JOBS", m.get_available_jobs())
    monkeypatch.setattr(settings, "lux_remote_dir", "/lustre/vista")
    monkeypatch.setattr(settings, "lux_account", "stf218")
    monkeypatch.setattr(settings, "session_id", "sess")
    conn = FakeSshConn(tmp_path)
    conn.on("sbatch --parsable", (0, "99\n", ""))

    async def fake_conn(ctx, tool, **kw):
        return conn

    monkeypatch.setattr(m, "_lux_conn", fake_conn)

    job_id, _, _, out_dir, nodes, duration = await m._submit_lux_job(
        None, "forge-pretrain", None, None, "MODEL=forge-m"
    )
    assert (job_id, nodes, duration) == ("99", 16, 1800)
    assert out_dir == "/lustre/vista/sess/out/99"
    src = "/lustre/vista/forge-pretrain/src"
    assert sorted(conn.puts) == [
        f"{src}/forge_common.sh",
        f"{src}/make_config.py",
        f"{src}/prepare_forge.sh",
    ]

    [(setup, _)] = conn.ran("prepare_forge.sh")
    assert "export FORGE_BRANCH=lux" in setup
    assert "export VISTA_JOB_DIR=/lustre/vista/forge-pretrain" in setup

    [(_, script)] = conn.ran("sbatch --parsable")
    assert "#SBATCH -A stf218" in script
    assert "#SBATCH -N 16" in script
    assert "#SBATCH -t 0:30:00" in script
    # As forge's job.sb: without tasks per node the batch env has no
    # SLURM_NTASKS for DeepSpeed's srun --export=ALL to hand the ranks.
    assert "#SBATCH --ntasks-per-node=8" in script
    assert "#SBATCH --gpus-per-node=8" in script
    assert f"export FORGE_DATA_DIR={DATA}" in script
    assert "set -- MODEL=forge-m" in script
    assert 'forge_launch lux.yml "${RUN_DIR_Lux}"' in script


@pytest.mark.anyio
async def test_real_job_dir_submits_on_frontier_under_the_tokens_project(monkeypatch):
    from fakes import FakeGlobusClient, FakeIriClient
    from vista_mcp_server.lib.user_config import UserConfig

    base = "/lustre/orion/chm243/proj-shared/vista"
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", JOB_DIR.parent)
    monkeypatch.setattr(m, "AVAILABLE_JOBS", m.get_available_jobs())
    monkeypatch.setattr(settings, "frontier_remote_dir", base)
    monkeypatch.setattr(settings, "frontier_globus_collection_id", "fr-coll")
    monkeypatch.setattr(settings, "session_id", "sess")
    iri, globus = FakeIriClient(job_id="777"), FakeGlobusClient()
    seen: dict = {}

    async def introspect(token, *, introspect_url):
        seen["introspected"] = token
        return "chm243"

    async def olcf(*, iri_token):
        seen["iri_token"] = iri_token
        return iri

    monkeypatch.setattr(m, "get_s3m_token_project", introspect)
    monkeypatch.setattr(m, "create_olcf_iri_client", olcf)
    monkeypatch.setattr(m, "create_globus_client", lambda **kw: globus)

    cfg = UserConfig(
        frontier_s3m_token="chm243-token",
        frontier_globus_token="g-transfer",
        frontier_globus_https_token="g-https",
    )
    job_id, log_path, err_path, out_dir, nodes, duration = await m._submit_frontier_job(
        cfg, "forge-pretrain", None, None, "MODEL=forge-s"
    )
    assert (job_id, nodes, duration) == ("777", 16, 1800)
    assert out_dir == f"{base}/sess/out/777"
    # The account is whatever project the token belongs to.
    assert seen == {"introspected": "chm243-token", "iri_token": "chm243-token"}

    [(spec, _)] = iri.submitted
    attrs = spec["attributes"]
    assert attrs["account"] == "chm243"
    assert attrs["queue_name"] == "batch"
    assert attrs["directory"] == f"{base}/sess"
    env = attrs["environment"]
    assert env["VISTA_JOB_DIR"] == f"{base}/forge-pretrain"
    assert env["RUN_DIR_Frontier"] == f"{base}/forge-pretrain/src"
    assert env["FORGE_BRANCH"] == "lux"
    assert env["FORGE_DEFAULT_MODEL"] == "forge-s"
    uploaded = sorted(path for _, path in globus.uploads)
    assert uploaded == [
        f"{base}/forge-pretrain/src/{f}"
        for f in ("forge_common.sh", "make_config.py", "prepare_forge.sh")
    ]
    body = spec["arguments"][-1]
    assert "set -- MODEL=forge-s" in body
    assert "module load xforge" in body
