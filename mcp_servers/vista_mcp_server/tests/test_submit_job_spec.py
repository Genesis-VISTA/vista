"""
JobSpec construction tests for Odo / Perlmutter / Frontier submit paths.

IRI and S3 are faked — no network. Asserts Slurm inlining, the
VISTA_OUT / VISTA_SCRATCH contract, setup/pre_launch, source staging, and the
OLCF output-push wiring.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import vista_mcp_server.submit_job_mcp as submit_job_mcp
from vista_mcp_server.config import settings
from vista_mcp_server.lib.user_config import UserConfig
from vista_mcp_server.submit_job_mcp import (
    AVAILABLE_JOBS,
    SubmittedJob,
    _get_olcf_job_outputs,
    _get_olcf_job_status,
    _get_perlmutter_job_status,
    _record_submitted_job,
    _submit_olcf_job,
    _submit_perlmutter_job,
    _submitted_jobs,
)
from fakes import FakeIriClient, FakeS3Client

REPO_ROOT = Path(__file__).resolve().parents[3]
HPC_JOBS_DIR = REPO_ROOT / "hpc_jobs"

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture(autouse=True)
def _hpc_jobs_and_registry(monkeypatch):
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", HPC_JOBS_DIR)
    monkeypatch.setattr(settings, "odo_remote_dir", "/fake/odo/vista")
    monkeypatch.setattr(settings, "frontier_remote_dir", "/fake/frontier/vista")
    monkeypatch.setattr(settings, "odo_account", "gen150-vista")
    monkeypatch.setattr(settings, "frontier_account", "chm243")
    monkeypatch.setattr(settings, "session_id", "test-session")
    monkeypatch.setattr(settings.s3, "bucket", "vista-test-bucket")
    monkeypatch.setattr(settings.s3, "key_id", "AKIAFAKE")
    monkeypatch.setattr(settings.s3, "secret", "fake-secret")
    _submitted_jobs.clear()
    yield
    _submitted_jobs.clear()


@pytest.fixture
def user_cfg() -> UserConfig:
    return UserConfig(
        odo_s3m_token="odo-token",
        frontier_s3m_token="frontier-token",
        nersc_iri_token="nersc-token",
        nersc_account="m1234",
        nersc_remote_dir="/fake/nersc/home/user/vista",
    )


def _patch_clients(monkeypatch, *, iri: FakeIriClient, s3: FakeS3Client | None = None):
    async def _iri(*, iri_token: str):
        return iri

    async def _noop_access(cfg, cluster):
        return None

    monkeypatch.setattr(submit_job_mcp, "create_odo_iri_client", _iri)
    monkeypatch.setattr(submit_job_mcp, "create_olcf_iri_client", _iri)
    monkeypatch.setattr(submit_job_mcp, "create_iri_client", _iri)
    monkeypatch.setattr(submit_job_mcp, "_require_olcf_access", _noop_access)
    if s3 is not None:
        monkeypatch.setattr(submit_job_mcp, "create_s3_client", lambda **kwargs: s3)


async def test_submit_odo_job_inlines_slurm_and_vista_out(monkeypatch, user_cfg):
    assert "example" in AVAILABLE_JOBS
    iri = FakeIriClient(job_id="odo-123")
    _patch_clients(monkeypatch, iri=iri)

    job_id, log_path, out_dir, nodes, duration = await _submit_olcf_job(
        user_cfg,
        "example",
        node_count=None,
        duration_int=None,
        script_args="a b",
        cluster="odo",
    )

    assert job_id == "odo-123"
    assert nodes == 1
    assert duration == 120  # example cluster_defaults
    assert log_path.endswith("log-odo-123.out")
    assert out_dir.endswith("/out/odo-123")

    assert len(iri.submitted) == 1
    spec, name = iri.submitted[0]
    assert name == "vista-example"
    assert spec["executable"] == "bash"
    job_cmd = spec["arguments"][2]
    assert (
        'export VISTA_OUT="/fake/odo/vista/test-session/out/$SLURM_JOB_ID"' in job_cmd
    )
    # Slurm script body is inlined
    slurm = (HPC_JOBS_DIR / "example" / "job.odo.slurm").read_text()
    assert slurm in job_cmd
    assert "set -- a b" in job_cmd
    env = spec["attributes"]["environment"]
    assert env["RUN_DIR_Odo"] == "/fake/odo/vista/test-session/src/example"
    assert spec["attributes"]["account"] == "gen150-vista"


async def test_odo_job_pushes_output_and_isolates_scratch(monkeypatch, user_cfg):
    """The S3 push contract, the scratch split, and the body isolation."""
    iri = FakeIriClient(job_id="odo-9")
    _patch_clients(monkeypatch, iri=iri)

    await _submit_olcf_job(
        user_cfg,
        "example",
        node_count=None,
        duration_int=None,
        script_args=None,
        cluster="odo",
    )
    spec, _ = iri.submitted[0]
    job_cmd = spec["arguments"][2]

    # Push credentials reach the job through the JobSpec environment...
    assert "export VISTA_S3_BUCKET=vista-test-bucket" in job_cmd
    assert "export VISTA_S3_KEY_ID=AKIAFAKE" in job_cmd
    assert "export VISTA_S3_SECRET=fake-secret" in job_cmd
    # ...under VISTA_* names, never AWS_*, which VISTAGuard's G5 gate denylists.
    assert "AWS_SECRET_ACCESS_KEY" not in job_cmd
    assert "AWS_ACCESS_KEY_ID" not in job_cmd
    # The key prefix is resolved at runtime: the job id isn't known at build
    # time. It carries the cluster, because Odo and Frontier job ids collide.
    assert 'export VISTA_S3_PREFIX="jobs/odo/$SLURM_JOB_ID"' in job_cmd

    # Scratch is exported and HOME points at it, so dotfiles are not uploaded.
    assert (
        'export VISTA_SCRATCH="/fake/odo/vista/test-session/scratch/$SLURM_JOB_ID"'
        in job_cmd
    )
    assert 'export HOME="${HOME:-$VISTA_SCRATCH}"' in job_cmd
    assert "$VISTA_OUT" not in job_cmd.split("export HOME=")[1].split("\n")[0]

    # $VISTA_KEEP is the third category: kept on the cluster, never uploaded.
    # It has to sit outside $VISTA_OUT (or the walk would upload it), outside
    # $VISTA_SCRATCH (or the trap would delete it), and outside the session dir
    # (or a later session could not find a checkpoint to --resume-from).
    assert 'export VISTA_KEEP="/fake/odo/vista/keep/$SLURM_JOB_ID"' in job_cmd
    assert "test-session" not in job_cmd.split("export VISTA_KEEP=")[1].split("\n")[0]
    assert 'mkdir -p "$VISTA_OUT" "$VISTA_SCRATCH/.vista" "$VISTA_KEEP"' in job_cmd

    # The uploader is materialized and armed on exit, and cleans scratch —
    # scratch only, so $VISTA_KEEP survives the job.
    assert "s3_put.py" in job_cmd
    assert "trap _vista_push_outputs EXIT" in job_cmd
    assert 'rm -rf "$VISTA_SCRATCH"' in job_cmd
    assert 'rm -rf "$VISTA_KEEP"' not in job_cmd

    # The body runs in a subshell so its own `trap ... EXIT` or `exec` cannot
    # replace the push trap.
    slurm = (HPC_JOBS_DIR / "example" / "job.odo.slurm").read_text()
    assert f"(\n{slurm}\n)" in job_cmd


async def test_olcf_pre_launch_stages_sources(monkeypatch, user_cfg):
    """Sources are inlined into pre_launch, which runs before the body's checks."""
    iri = FakeIriClient(job_id="odo-11")
    _patch_clients(monkeypatch, iri=iri)

    await _submit_olcf_job(
        user_cfg,
        "salt-neutronics-tbr",
        node_count=None,
        duration_int=None,
        script_args=None,
        cluster="odo",
    )
    spec, _ = iri.submitted[0]
    pre_launch = spec["attributes"]["pre_launch"]

    # Recover the shell payload so the assertions also prove the quoting is
    # correct (the setup scripts contain both quote characters).
    import shlex

    argv = shlex.split(pre_launch)
    assert argv[:2] == ["bash", "-lc"]
    script = argv[2]

    # The staged file is base64-embedded and written atomically...
    assert "run_state_point.py" in script
    assert "base64 -d" in script
    assert "mv -f" in script
    # ...and setup_odo.sh (which validates it) is appended after the staging.
    setup = (HPC_JOBS_DIR / "salt-neutronics-tbr" / "setup_odo.sh").read_text()
    assert setup in script
    assert script.index("base64 -d") < script.index(setup)
    # Exactly the supporting files are written — job/setup scripts are inlined
    # into the JobSpec instead, so they must not appear as staged files.
    import re

    written = {Path(m).name for m in re.findall(r"base64 -d > (\S+)\.\$\$", script)}
    assert written == {"run_state_point.py"}, written


async def test_submit_odo_job_requires_push_credentials(monkeypatch, user_cfg):
    """A job that cannot phone home must never be launched."""
    from fastmcp.exceptions import ToolError

    monkeypatch.setattr(settings.s3, "secret", None)
    iri = FakeIriClient(job_id="odo-x")
    _patch_clients(monkeypatch, iri=iri)

    with pytest.raises(ToolError, match="No S3 credentials configured"):
        await _submit_olcf_job(
            user_cfg,
            "example",
            node_count=None,
            duration_int=None,
            script_args=None,
            cluster="odo",
        )
    assert not iri.submitted, "must fail before submitting"


async def test_submit_perlmutter_job_inlines_slurm_and_uploads(monkeypatch, user_cfg):
    assert "forge-tune" in AVAILABLE_JOBS
    iri = FakeIriClient(job_id="pm-99")
    _patch_clients(monkeypatch, iri=iri)

    job_id, log_path, out_dir, nodes, duration = await _submit_perlmutter_job(
        user_cfg,
        "forge-tune",
        node_count=2,
        duration_int=900,
        script_args=None,
    )

    assert job_id == "pm-99"
    assert nodes == 2
    assert duration == 900
    assert "test-session/out" in log_path

    spec, name = iri.submitted[0]
    assert name == "vista-forge-tune"
    job_cmd = spec["arguments"][2]
    assert "export VISTA_OUT=" in job_cmd
    # Exported at NERSC too, so job scripts stay portable across clusters.
    assert "export VISTA_SCRATCH=" in job_cmd
    assert (
        'export VISTA_KEEP="/fake/nersc/home/user/vista/keep/$SLURM_JOB_ID"' in job_cmd
    )
    # No S3 push here: Perlmutter's IRI token authorizes storage.
    assert "VISTA_S3_BUCKET" not in job_cmd
    slurm = (HPC_JOBS_DIR / "forge-tune" / "job.perlmutter.slurm").read_text()
    assert slurm in job_cmd
    # setup_perlmutter.sh exists → pre_launch set
    assert "pre_launch" in spec["attributes"]
    assert "bash -lc" in spec["attributes"]["pre_launch"]
    env = spec["attributes"]["environment"]
    assert env["RUN_DIR_Perlmutter"].endswith("/forge-tune/src")
    assert "VISTA_PM_IMAGE" in env
    assert iri.mkdirs  # out dir
    assert iri.uploads  # source files


async def test_submit_frontier_job_stages_and_inlines(monkeypatch, user_cfg):
    iri = FakeIriClient(job_id="fr-7")
    _patch_clients(monkeypatch, iri=iri)

    job_id, log_path, out_dir, nodes, duration = await _submit_olcf_job(
        user_cfg,
        "example",
        node_count=None,
        duration_int=None,
        script_args=None,
        cluster="frontier",
    )

    assert job_id == "fr-7"
    assert nodes == 1
    assert duration == 600
    spec, _ = iri.submitted[0]
    job_cmd = spec["arguments"][2]
    assert "VISTA_OUT=" in job_cmd
    assert "VISTA_SCRATCH=" in job_cmd
    assert "trap _vista_push_outputs EXIT" in job_cmd
    assert (HPC_JOBS_DIR / "example" / "job.frontier.slurm").read_text() in job_cmd
    run_dir = spec["attributes"]["environment"].get("RUN_DIR_Frontier")
    assert run_dir == "/fake/frontier/vista/test-session/src/example"
    # Unlike Odo, Frontier does not cd into the source dir.
    assert f'cd "{run_dir}"' not in job_cmd
    # Its push lands under its own cluster prefix, never Odo's.
    assert 'export VISTA_S3_PREFIX="jobs/frontier/$SLURM_JOB_ID"' in job_cmd
    assert "base64 -d" in spec["attributes"]["pre_launch"]


async def test_olcf_status_reads_pushed_log_and_listing(monkeypatch, user_cfg):
    iri = FakeIriClient(status={"state": "COMPLETED", "exit_code": 0})
    s3 = FakeS3Client()
    s3.seed_job_output(
        "jobs/odo/odo-5", {"chart.png": b"png", "sub/results.json": b"{}"}
    )
    _patch_clients(monkeypatch, iri=iri, s3=s3)

    text = await _get_olcf_job_status(user_cfg, "odo-5", cluster="odo")

    assert "JOB_ID=odo-5" in text
    assert "STATE=COMPLETED" in text
    assert "--- LOGS ---" in text
    assert "line1" in text
    assert "chart.png" in text
    assert "sub/results.json" in text
    assert "WARNING" not in text


async def test_olcf_job_keys_are_namespaced_per_cluster(
    monkeypatch, user_cfg, tmp_path
):
    """
    Odo and Frontier are separate Slurm installs with independent job id
    counters sharing one bucket, so the same numeric id can be live on both.
    The cluster belongs in the key: without it the two trees overwrite each
    other, and since the prefix is what authorizes a read, passing a Frontier
    id with `cluster="odo"` would hand moderate-enclave output to a caller
    holding only an open-enclave token.
    """
    iri = FakeIriClient(status={"state": "COMPLETED", "exit_code": 0})
    s3 = FakeS3Client()
    s3.seed_job_output("jobs/odo/4242", {"open.json": b"{}"})
    s3.seed_job_output("jobs/frontier/4242", {"moderate.json": b"{}"})
    _patch_clients(monkeypatch, iri=iri, s3=s3)

    odo = await _get_olcf_job_status(user_cfg, "4242", cluster="odo")
    assert "open.json" in odo and "moderate.json" not in odo

    frontier = await _get_olcf_job_status(user_cfg, "4242", cluster="frontier")
    assert "moderate.json" in frontier and "open.json" not in frontier

    # And the moderate-enclave object is unreachable through the open enclave.
    with pytest.raises(FileNotFoundError):
        await _get_olcf_job_outputs(
            user_cfg, tmp_path, "4242", ["moderate.json"], cluster="odo"
        )


async def test_olcf_status_distinguishes_empty_log_from_missing(monkeypatch, user_cfg):
    """
    A job that wrote nothing to stdout still uploads a zero-byte log.out.
    Reporting that as "not yet" for a finished job sends the agent looking for
    an upload that already happened.
    """
    iri = FakeIriClient(status={"state": "COMPLETED", "exit_code": 0})
    s3 = FakeS3Client()
    s3.objects["jobs/odo/odo-9/log.out"] = b""
    s3.objects["jobs/odo/odo-9/manifest.json"] = b"{}"
    _patch_clients(monkeypatch, iri=iri, s3=s3)

    text = await _get_olcf_job_status(user_cfg, "odo-9", cluster="odo")

    assert "empty" in text
    assert "no logs yet" not in text


async def test_olcf_status_flags_truncated_upload(monkeypatch, user_cfg):
    """A finished job with no manifest produced a partial upload, not no output."""
    iri = FakeIriClient(status={"state": "COMPLETED", "exit_code": 0})
    s3 = FakeS3Client()
    s3.objects["jobs/odo/odo-6/out/partial.bin"] = b"x"
    _patch_clients(monkeypatch, iri=iri, s3=s3)

    text = await _get_olcf_job_status(user_cfg, "odo-6", cluster="odo")

    assert "output upload did not complete" in text
    assert "partial.bin" in text


async def test_olcf_status_skips_s3_before_the_job_runs(monkeypatch, user_cfg):
    iri = FakeIriClient(status={"state": "QUEUED"})
    s3 = FakeS3Client()
    _patch_clients(monkeypatch, iri=iri, s3=s3)

    text = await _get_olcf_job_status(user_cfg, "odo-7", cluster="odo")

    assert "has not started yet" in text
    assert not s3.list_calls, "queued jobs must not cost an S3 round trip"


async def test_olcf_outputs_download_and_cache(monkeypatch, user_cfg, tmp_path):
    iri = FakeIriClient()
    s3 = FakeS3Client()
    s3.seed_job_output("jobs/odo/odo-8", {"chart.png": b"pngdata"})
    _patch_clients(monkeypatch, iri=iri, s3=s3)

    text = await _get_olcf_job_outputs(
        user_cfg, tmp_path, "odo-8", ["chart.png"], cluster="odo"
    )

    assert text.splitlines()[1] == "/mnt/data/output/odo-8/chart.png"
    assert (tmp_path / "odo-8" / "chart.png").read_bytes() == b"pngdata"
    assert len(s3.downloads) == 1

    # A second call is served from the local copy — no repeat download.
    await _get_olcf_job_outputs(
        user_cfg, tmp_path, "odo-8", ["chart.png"], cluster="odo"
    )
    assert len(s3.downloads) == 1


@pytest.mark.parametrize("bad", ["../escape.txt", "/etc/passwd", "a/../../b"])
async def test_olcf_outputs_reject_path_traversal(monkeypatch, user_cfg, tmp_path, bad):
    iri = FakeIriClient()
    s3 = FakeS3Client()
    _patch_clients(monkeypatch, iri=iri, s3=s3)

    with pytest.raises(ValueError, match="Invalid path"):
        await _get_olcf_job_outputs(user_cfg, tmp_path, "odo-8", [bad], cluster="odo")
    assert not s3.downloads


async def test_perlmutter_status_formats_golden_fixture(
    monkeypatch, user_cfg, tmp_path
):
    """Status text matches the KEY=VALUE shape campaign parsers expect."""
    fixture = Path(__file__).parent / "fixtures" / "iri_status_completed.json"
    import json

    status = json.loads(fixture.read_text())
    iri = FakeIriClient(status=status)
    iri.head_content["/remote/log.out"] = "line1\nline2\n"

    async def _nersc(*, iri_token: str):
        return iri

    monkeypatch.setattr(submit_job_mcp, "create_iri_client", _nersc)
    _record_submitted_job(
        "pm-1",
        SubmittedJob(
            cluster="perlmutter", log_path="/remote/log.out", output_dir="/remote/out"
        ),
    )

    text = await _get_perlmutter_job_status(user_cfg, "pm-1")
    assert "JOB_ID=pm-1" in text
    assert "STATE=COMPLETED" in text
    assert "EXIT_CODE=0" in text
    assert "--- LOGS ---" in text
    assert "line1" in text
