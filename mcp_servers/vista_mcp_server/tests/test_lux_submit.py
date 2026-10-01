"""
Lux dispatch: Slurm over SSH (no IRI). The SSH connection is faked -- commands
are scripted and SFTP is served from a temp dir -- so these check what VISTA
sends to the login node and how it reads the answers back.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

import vista_mcp_server.submit_job_mcp as m
from vista_mcp_server.config import settings
from vista_mcp_server.lib import slurm_ssh
from vista_mcp_server.lib.user_config import UserConfig
from fakes import FakeSshConn

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

BASE = "/lustre/orion/abc123/proj-shared/vista"
CFG = UserConfig(lux_remote_dir=BASE)


def _write_job(root: Path, *, setup: str | None = "echo setup-ran\n") -> Path:
    job = root / "lux-demo"
    job.mkdir(parents=True)
    (job / "README.md").write_text("# lux-demo\n\nDemo job.\n", encoding="utf-8")
    (job / "cluster_defaults.json").write_text(
        json.dumps(
            {
                "lux": {
                    "duration": 1800,
                    "resources": {"node_count": 16, "exclusive_node_use": True},
                    "iri": {"environment": {"FOO": "bar baz"}},
                }
            }
        ),
        encoding="utf-8",
    )
    (job / "job.lux.slurm").write_text(
        '#!/bin/bash -l\n#SBATCH -A ignored\necho "run $@"\n', encoding="utf-8"
    )
    (job / "run.py").write_text("print('hi')\n", encoding="utf-8")
    if setup is not None:
        (job / "setup_lux.sh").write_text(setup, encoding="utf-8")
    return job


@pytest.fixture
def lux(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "hpc_jobs"
    _write_job(jobs_dir)
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", jobs_dir)
    monkeypatch.setattr(m, "AVAILABLE_JOBS", m.get_available_jobs())
    monkeypatch.setattr(settings, "lux_proxy", "http://proxy.ccs.ornl.gov:3128")

    conn = FakeSshConn(tmp_path / "remote")
    conn.on("sbatch --parsable", (0, "4242\n", ""))
    labels: list[str] = []

    async def fake_lux_conn(ctx, tool, **call_args):
        labels.append(tool)
        return conn

    monkeypatch.setattr(m, "_lux_conn", fake_lux_conn)
    conn.labels = labels
    return conn


def _submitted_script(conn: FakeSshConn) -> str:
    [(_, script)] = conn.ran("sbatch --parsable")
    return script


async def test_submit_renders_sbatch_header_and_env(lux):
    job_id, log_path, err_path, out_dir, nodes, duration = await m._submit_lux_job(
        None, CFG, "lux-demo", None, None, "MODEL=forge-m --flag"
    )
    assert job_id == "4242"
    assert (nodes, duration) == (16, 1800)
    assert log_path == f"{BASE}/out/log-4242.out"
    assert err_path == f"{BASE}/out/log-4242.err"
    assert out_dir == f"{BASE}/out/4242"

    script = _submitted_script(lux)
    header = [line for line in script.splitlines() if line.startswith("#SBATCH")]
    assert header[:6] == [
        "#SBATCH -J vista-lux-demo",
        "#SBATCH -N 16",
        "#SBATCH -t 0:30:00",
        f"#SBATCH -o {BASE}/out/log-%j.out",
        f"#SBATCH -e {BASE}/out/log-%j.err",
        f"#SBATCH --chdir={BASE}",
    ]
    assert "#SBATCH --exclusive" in header
    # No account: the job runs as the researcher, so Slurm charges their default.
    # The only -A is the job script's own, which comes after commands (below).
    assert [line for line in header if line.startswith("#SBATCH -A")] == [
        "#SBATCH -A ignored"
    ]
    # Not requested by the demo job: no per-node tasks / GPUs directives.
    assert not any("--ntasks-per-node" in line or "--gpus" in line for line in header)
    assert not any(
        line.startswith("#SBATCH -q") for line in header
    )  # no IRI default queue
    # The job's own directive comes after commands, so Slurm ignores it.
    assert script.index("#SBATCH -A ignored") > script.index("export RUN_DIR_Lux")
    assert f"export RUN_DIR_Lux={BASE}/lux-demo/src" in script
    assert "export FOO='bar baz'" in script
    assert f'export VISTA_OUT={BASE}/out/"$SLURM_JOB_ID"' in script
    assert 'mkdir -p -m 2775 "$VISTA_OUT"' in script
    assert "export https_proxy=http://proxy.ccs.ornl.gov:3128" in script
    assert "set -- MODEL=forge-m --flag" in script


async def test_submit_uploads_sources_but_not_metadata(lux):
    await m._submit_lux_job(None, CFG, "lux-demo", 2, 600, None)
    # README / cluster_defaults / job.lux.slurm / setup_lux.sh are inlined or run
    # by VISTA, never uploaded.
    assert lux.puts == [f"{BASE}/lux-demo/src/run.py"]
    # Only the source tree is made ahead of time; Slurm makes out/.
    assert not lux.local(f"{BASE}/out").exists()

    lux.puts.clear()
    await m._submit_lux_job(None, CFG, "lux-demo", 2, 600, None)
    assert lux.puts == []  # already there at full length


async def test_setup_runs_on_login_node_with_env_before_sbatch(lux):
    await m._submit_lux_job(None, CFG, "lux-demo", None, None, None)
    [(setup_cmd, _)] = lux.ran("echo setup-ran")
    assert f"export RUN_DIR_Lux={BASE}/lux-demo/src" in setup_cmd
    assert f"export VISTA_JOB_DIR={BASE}/lux-demo" in setup_cmd
    assert "export https_proxy=http://proxy.ccs.ornl.gov:3128" in setup_cmd
    order = [c for c, _ in lux.commands]
    assert order.index(setup_cmd) < order.index("sbatch --parsable")


async def test_setup_failure_is_a_tool_error_and_nothing_is_submitted(lux):
    lux.handlers.insert(
        0, ("echo setup-ran", lambda c, i: (1, "", "git: proxy refused"))
    )
    with pytest.raises(ToolError, match="proxy refused"):
        await m._submit_lux_job(None, CFG, "lux-demo", None, None, None)
    assert lux.ran("sbatch") == []


async def test_sbatch_rejection_is_a_tool_error(lux):
    lux.handlers.insert(
        0,
        (
            "sbatch --parsable",
            lambda c, i: (1, "", "sbatch: error: Invalid account or account/partition"),
        ),
    )
    with pytest.raises(ToolError, match="Invalid account"):
        await m._submit_lux_job(None, CFG, "lux-demo", None, None, None)


async def test_status_reports_state_log_tail_and_outputs(lux, tmp_path):
    job_id, log_path, err_path, out_dir, *_ = await m._submit_lux_job(
        None, CFG, "lux-demo", None, None, None
    )
    lux.on("squeue", (0, "RUNNING|None\n", ""))
    lux.local(log_path).parent.mkdir(parents=True)  # what Slurm does at job start
    lux.local(log_path).write_text(
        "step 1 loss 9.1\nstep 2 loss 8.7\n", encoding="utf-8"
    )
    (lux.local(out_dir) / "checkpoints").mkdir(parents=True)
    (lux.local(out_dir) / "checkpoints" / "latest").write_text("1", encoding="utf-8")
    lux.on("find .", (0, "checkpoints/latest\n", ""))

    host_out = tmp_path / "host_out"
    text = await m._get_lux_job_status(None, CFG, host_out, job_id)
    assert "STATE=ACTIVE" in text
    assert "SLURM_STATE=RUNNING" in text
    assert "step 2 loss 8.7" in text
    assert "checkpoints/latest" in text
    assert "--- STDERR ---\n\n(nothing on stderr)" in text  # no .err file yet

    # A failing rank's traceback lands in stderr, and status must show it.
    lux.local(err_path).write_text(
        "Traceback (most recent call last):\nKeyError: 'SLURM_NTASKS'\n",
        encoding="utf-8",
    )
    text = await m._get_lux_job_status(None, CFG, host_out, job_id)
    assert "KeyError: 'SLURM_NTASKS'" in text.split("--- STDERR ---")[1]

    # Incremental: the next poll only fetches what was appended.
    with lux.local(log_path).open("a", encoding="utf-8") as f:
        f.write("step 3 loss 8.2\n")
    text = await m._get_lux_job_status(None, CFG, host_out, job_id)
    assert "step 3 loss 8.2" in text
    local_log = host_out / job_id / "log-4242.out"
    assert local_log.read_text(encoding="utf-8").count("step 1") == 1


async def test_status_of_pending_job_skips_log_fetch(lux, tmp_path):
    lux.on("squeue", (0, "PENDING|Priority\n", ""))
    text = await m._get_lux_job_status(None, CFG, tmp_path, "77")
    assert "STATE=PENDING" in text
    assert "REASON=Priority" in text
    assert "has not started yet" in text


async def test_status_falls_back_to_sacct_after_job_leaves_queue(lux, tmp_path):
    lux.on("squeue", (0, "", ""))
    lux.on("sacct", (0, "TIMEOUT|0:0\n", ""))
    text = await m._get_lux_job_status(None, CFG, tmp_path, "78")
    assert "STATE=FAILED" in text
    assert "SLURM_STATE=TIMEOUT" in text
    assert "EXIT_CODE=0:0" in text
    assert "(no logs yet)" in text  # log file never created: not an error


async def test_outputs_download_over_sftp_and_cache_locally(lux, tmp_path):
    out_dir = f"{BASE}/out/79"
    lux.local(out_dir).mkdir(parents=True)
    (lux.local(out_dir) / "metrics.csv").write_bytes(b"iter,loss\n1,9.1\n")

    text = await m._get_lux_job_outputs(None, CFG, tmp_path, "79", ["metrics.csv"])
    assert "/mnt/data/output/79/metrics.csv" in text
    assert (tmp_path / "79" / "metrics.csv").read_bytes() == b"iter,loss\n1,9.1\n"

    lux.labels.clear()
    await m._get_lux_job_outputs(None, CFG, tmp_path, "79", ["metrics.csv"])
    assert lux.labels == []  # served from local cache, no login needed

    with pytest.raises(ValueError):
        await m._get_lux_job_outputs(None, CFG, tmp_path, "79", ["../escape"])


async def test_lux_hello_is_one_small_node_and_runs_nothing_on_the_login_node(
    lux, monkeypatch
):
    """The repo's lux-hello job: the cheap way to check Lux end to end."""
    repo_jobs = Path(__file__).resolve().parents[3] / "hpc_jobs"
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", repo_jobs)
    monkeypatch.setattr(m, "AVAILABLE_JOBS", m.get_available_jobs())

    job_id, *_, nodes, duration = await m._submit_lux_job(
        None, CFG, "lux-hello", None, None, None
    )
    assert job_id == "4242"
    assert (nodes, duration) == (1, 300)

    script = _submitted_script(lux)
    header = [line for line in script.splitlines() if line.startswith("#SBATCH")]
    assert "#SBATCH -N 1" in header
    assert "#SBATCH -t 0:05:00" in header
    assert "#SBATCH --exclusive" not in header
    assert not any("--gpus" in line or "--ntasks-per-node" in line for line in header)
    # No setup_lux.sh and no sources: besides creating directories, sbatch is the
    # only command, and nothing is uploaded.
    assert [c for c, _ in lux.commands if not c.startswith("mkdir -p ")] == [
        "sbatch --parsable"
    ]
    assert lux.puts == []
    assert 'tee "${VISTA_OUT}/hello.txt"' in script


async def test_lux_is_never_the_implicit_default():
    # No token selects Lux, so a call without cluster= must not land there.
    with pytest.raises(ToolError, match="No HPC cluster configured"):
        m._resolve_cluster(None, UserConfig())
    assert m._resolve_cluster("lux", UserConfig()) == "lux"


async def test_unset_remote_dir_is_refused_before_signing_in(lux):
    with pytest.raises(ToolError, match="Lux remote directory"):
        await m._submit_lux_job(None, UserConfig(), "lux-demo", None, None, None)
    assert lux.labels == []  # never asked for passcodes
    assert lux.commands == []


async def test_missing_lux_section_is_rejected(lux, monkeypatch):
    info = m.AVAILABLE_JOBS["lux-demo"]
    monkeypatch.setattr(info, "cluster_defaults", m.ClusterDefaults())
    with pytest.raises(ValueError, match='no "lux" section'):
        await m._submit_lux_job(None, CFG, "lux-demo", None, None, None)


# ------------------------------------------------------------------ slurm_ssh units


@pytest.mark.parametrize(
    ("slurm", "expected"),
    [
        ("RUNNING", "ACTIVE"),
        ("COMPLETING", "ACTIVE"),
        ("PENDING", "PENDING"),
        ("COMPLETED", "COMPLETED"),
        ("CANCELLED by 12345", "CANCELED"),
        ("CANCELLED+", "CANCELED"),
        ("TIMEOUT", "FAILED"),
        ("NODE_FAIL", "FAILED"),
        ("OUT_OF_MEMORY", "FAILED"),
        ("", "UNKNOWN"),
    ],
)
async def test_normalize_state(slurm, expected):
    assert slurm_ssh.normalize_state(slurm) == expected


async def test_sbatch_takes_job_id_after_warnings(tmp_path):
    conn = FakeSshConn(tmp_path)
    conn.on("sbatch", (0, "sbatch: warning: something\n5150;lux\n", ""))
    assert await slurm_ssh.sbatch(conn, "#!/bin/bash\n") == "5150"


async def test_sbatch_without_job_id_raises(tmp_path):
    conn = FakeSshConn(tmp_path)
    conn.on("sbatch", (0, "not a job id\n", ""))
    with pytest.raises(slurm_ssh.SlurmSshError):
        await slurm_ssh.sbatch(conn, "#!/bin/bash\n")


async def test_scancel_failure_raises(tmp_path):
    conn = FakeSshConn(tmp_path)
    conn.on("scancel", (1, "", "scancel: error: Invalid job id specified"))
    with pytest.raises(slurm_ssh.SlurmSshError, match="Invalid job id"):
        await slurm_ssh.scancel(conn, "999")


async def test_render_batch_script_optional_directives():
    script = slurm_ssh.render_batch_script(
        job_name="j",
        node_count=1,
        duration_s=3725,
        stdout_path="o",
        stderr_path="e",
        workdir="w",
        body="echo hi\n",
        queue="debug",
        constraint="nvme",
    )
    assert "#SBATCH -t 1:02:05" in script
    assert "#SBATCH -q debug" in script
    assert "#SBATCH -C nvme" in script
    assert "--exclusive" not in script
    assert script.startswith("#!/bin/bash -l\n")
    assert script.endswith("echo hi\n")


async def test_render_batch_script_tasks_and_gpus_per_node():
    script = slurm_ssh.render_batch_script(
        job_name="j",
        node_count=2,
        duration_s=60,
        stdout_path="o",
        stderr_path="e",
        workdir="w",
        body="",
        ntasks_per_node=8,
        gpus_per_node=8,
    )
    assert "#SBATCH --ntasks-per-node=8" in script
    assert "#SBATCH --gpus-per-node=8" in script
