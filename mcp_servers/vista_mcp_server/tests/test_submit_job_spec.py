"""
JobSpec construction tests for Odo / Perlmutter / Frontier submit paths.

IRI and Globus are faked — no network. Asserts Slurm inlining, VISTA_OUT,
setup/pre_launch, source sync, and the one folder layout every cluster shares
(see `RemoteLayout`): status calls find a job's files from its id alone.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

import vista_mcp_server.submit_job_mcp as submit_job_mcp
from vista_mcp_server.config import settings
from vista_mcp_server.lib.user_config import UserConfig
from vista_mcp_server.submit_job_mcp import (
    AVAILABLE_JOBS,
    _get_job_status,
    _job_body,
    _submit_job,
)
from fakes import FakeGlobusClient, FakeIriClient

REPO_ROOT = Path(__file__).resolve().parents[3]
ODO = "/fake/odo/vista"
FRONTIER = "/fake/frontier/vista"
NERSC = "/fake/nersc/home/user/vista"
HPC_JOBS_DIR = REPO_ROOT / "hpc_jobs"

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture(autouse=True)
def _hpc_jobs(monkeypatch):
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", HPC_JOBS_DIR)
    monkeypatch.setattr(settings, "odo_globus_collection_id", "odo-collection")
    monkeypatch.setattr(
        settings, "frontier_globus_collection_id", "frontier-collection"
    )


@pytest.fixture
def user_cfg() -> UserConfig:
    return UserConfig(
        odo_s3m_token="odo-token",
        frontier_s3m_token="frontier-token",
        nersc_iri_token="nersc-token",
        nersc_account="m1234",
        nersc_remote_dir=NERSC,
        odo_remote_dir=ODO,
        frontier_remote_dir=FRONTIER,
        globus_token="fake-transfer-refresh",
        globus_https_token="fake-https-refresh",
    )


def _patch_clients(monkeypatch, *, iri: FakeIriClient, globus: FakeGlobusClient):
    async def _introspect(token, *, introspect_url):
        # Every token belongs to a project no deployment is configured for.
        return "abc123"

    monkeypatch.setattr(submit_job_mcp, "client_for", lambda cluster, token: iri)
    monkeypatch.setattr(submit_job_mcp, "create_globus_client", lambda **kwargs: globus)
    monkeypatch.setattr(submit_job_mcp, "get_s3m_token_project", _introspect)


async def test_submit_odo_job_inlines_slurm_and_vista_out(monkeypatch, user_cfg):
    assert "example" in AVAILABLE_JOBS
    iri = FakeIriClient(job_id="odo-123")
    globus = FakeGlobusClient()
    globus.seed_remote_dir(ODO)
    _patch_clients(monkeypatch, iri=iri, globus=globus)

    job_id, log_path, err_path, out_dir, nodes, duration = await _submit_job(
        "odo",
        user_cfg,
        "example",
        node_count=None,
        duration_int=None,
        script_args="a b",
    )

    assert job_id == "odo-123"
    assert nodes == 1
    assert duration == 120  # example cluster_defaults
    assert log_path == f"{ODO}.out/log-odo-123.out"
    # The stderr path is rendered beside stdout and was thrown away, which is why
    # a job that failed reached its caller with nothing to explain it.
    assert err_path == f"{ODO}.out/log-odo-123.err"
    assert out_dir == f"{ODO}.out/odo-123"

    assert len(iri.submitted) == 1
    spec, name = iri.submitted[0]
    assert name == "vista-example"
    assert spec["executable"] == "bash"
    job_cmd = spec["arguments"][2]
    assert f'export VISTA_OUT={ODO}.out/"$SLURM_JOB_ID"' in job_cmd
    assert 'mkdir -p -m 2775 "$VISTA_OUT"' in job_cmd
    # `.out` is shared with colleagues and with Lux, so the job keeps it
    # writable by the token's project group before anything else.
    assert job_cmd.startswith("umask 002\n")
    assert f"chgrp abc123 {ODO}.out 2>/dev/null || true" in job_cmd
    assert f"chmod 2775 {ODO}.out 2>/dev/null || true" in job_cmd
    assert spec["directory"] == f"{ODO}.researcher.jobs"
    assert spec["stdout_path"] == f"{ODO}.out/log-%j.out"
    # Nothing the job writes is made through Globus: only the source tree.
    assert globus.mkdir_p_calls == [
        ("odo-collection", f"{ODO}.researcher.jobs/example/src", "/fake/odo")
    ]
    # Slurm script body is inlined
    slurm = (HPC_JOBS_DIR / "example" / "job.odo.slurm").read_text(encoding="utf-8")
    assert slurm in job_cmd
    assert "set -- a b" in job_cmd
    env = spec["environment"]
    assert env["RUN_DIR_Odo"] == f"{ODO}.researcher.jobs/example/src"
    assert env["FORGE_MODEL_Odo"] == f"{ODO}.out/example/model"
    assert spec["attributes"]["account"] == "abc123"
    assert globus.uploads, "expected Globus source upload"


async def test_submit_perlmutter_job_inlines_slurm_and_uploads(monkeypatch, user_cfg):
    assert "forge-tune" in AVAILABLE_JOBS
    iri = FakeIriClient(job_id="pm-99")
    globus = FakeGlobusClient()
    _patch_clients(monkeypatch, iri=iri, globus=globus)

    job_id, log_path, err_path, out_dir, nodes, duration = await _submit_job(
        "perlmutter",
        user_cfg,
        "forge-tune",
        node_count=2,
        duration_int=900,
        script_args=None,
    )

    assert job_id == "pm-99"
    assert nodes == 2
    assert duration == 900
    assert (log_path, err_path, out_dir) == (
        f"{NERSC}/out/log-pm-99.out",
        f"{NERSC}/out/log-pm-99.err",
        f"{NERSC}/out/pm-99",
    )

    spec, name = iri.submitted[0]
    assert name == "vista-forge-tune"
    job_cmd = spec["arguments"][2]
    assert f'export VISTA_OUT={NERSC}/out/"$SLURM_JOB_ID"' in job_cmd
    # The researcher's own folder, which nothing else writes: NERSC's default
    # permissions are left alone (no group-writable prefix).
    assert "umask 002" not in job_cmd
    assert "chmod 2775" not in job_cmd
    assert "chgrp" not in job_cmd
    assert spec["directory"] == f"{NERSC}/jobs"
    slurm = (HPC_JOBS_DIR / "forge-tune" / "job.perlmutter.slurm").read_text(
        encoding="utf-8"
    )
    assert slurm in job_cmd
    # setup_perlmutter.sh runs inside the job, after VISTA_OUT, as on OLCF.
    setup = (HPC_JOBS_DIR / "forge-tune" / "setup_perlmutter.sh").read_text(
        encoding="utf-8"
    )
    assert "pre_launch" not in spec and "resource_id" not in spec["attributes"]
    assert (
        job_cmd.index("export VISTA_OUT=")
        < job_cmd.index(f"bash -lc {shlex.quote(setup)}")
        < job_cmd.index(slurm)
    )
    assert spec["attributes"]["account"] == "m1234"
    env = spec["environment"]
    assert env["RUN_DIR_Perlmutter"].endswith("/forge-tune/src")
    assert "VISTA_PM_IMAGE" in env
    # The source tree, and the log folder: Perlmutter runs as the researcher,
    # and NERSC's Slurm is not known to create a missing one.
    assert iri.mkdirs == [f"{NERSC}/jobs/forge-tune/src", f"{NERSC}/out"]
    assert iri.uploads  # source files


async def test_submit_frontier_job_syncs_and_inlines(monkeypatch, user_cfg):
    iri = FakeIriClient(job_id="fr-7")
    globus = FakeGlobusClient()
    globus.seed_remote_dir(FRONTIER)
    _patch_clients(monkeypatch, iri=iri, globus=globus)

    job_id, log_path, err_path, out_dir, nodes, duration = await _submit_job(
        "frontier",
        user_cfg,
        "example",
        node_count=None,
        duration_int=None,
        script_args=None,
    )

    assert job_id == "fr-7"
    assert nodes == 1
    assert duration == 600
    assert (log_path, err_path, out_dir) == (
        f"{FRONTIER}.out/log-fr-7.out",
        f"{FRONTIER}.out/log-fr-7.err",
        f"{FRONTIER}.out/fr-7",
    )
    spec, _ = iri.submitted[0]
    job_cmd = spec["arguments"][2]
    assert f'export VISTA_OUT={FRONTIER}.out/"$SLURM_JOB_ID"' in job_cmd
    assert 'mkdir -p -m 2775 "$VISTA_OUT"' in job_cmd
    assert spec["directory"] == f"{FRONTIER}.researcher.jobs"
    assert (HPC_JOBS_DIR / "example" / "job.frontier.slurm").read_text(
        encoding="utf-8"
    ) in job_cmd
    assert "VISTA_REMOTE_BASE" not in spec["environment"]  # never created
    run_dir = spec["environment"].get("RUN_DIR_Frontier")
    assert run_dir is not None and run_dir.endswith("/example/src")
    # Only the source tree: out/ was once made through Globus here, as the
    # researcher, where the project's automation user could not write to it.
    assert globus.mkdir_p_calls == [
        (
            "frontier-collection",
            f"{FRONTIER}.researcher.jobs/example/src",
            "/fake/frontier",
        )
    ]
    assert globus.uploads


async def test_odo_status_fetches_both_streams_and_shows_stderr(
    monkeypatch, user_cfg, tmp_path
):
    """
    The path every odo job actually takes, and the one that was untested.

    Job 44039 exited with status 2 having written only the setup script's echoes
    to stdout; the reason — argparse rejecting invented flags — was in
    `log-44039.err`, a file this function had never heard of. The agent that
    commissioned it was told "no outputs recorded" and reasonably concluded its
    test had produced nothing.
    """
    import json

    fixture = Path(__file__).parent / "fixtures" / "iri_status_completed.json"
    iri = FakeIriClient(status=json.loads(fixture.read_text(encoding="utf-8")))
    globus = FakeGlobusClient()
    globus.files[f"{ODO}.out/log-44039.out"] = (
        b"[setup_odo] OK: run_state_point.py present\n"
    )
    globus.files[f"{ODO}.out/log-44039.err"] = (
        b"run_state_point.py: error: unrecognized arguments: --salt flibe_90Li6\n"
    )
    _patch_clients(monkeypatch, iri=iri, globus=globus)

    text = await _get_job_status(user_cfg, tmp_path, "44039", cluster="odo")

    assert "--- LOGS ---" in text
    assert "run_state_point.py present" in text
    assert "--- STDERR ---" in text
    assert "unrecognized arguments: --salt flibe_90Li6" in text
    # Both streams are tailed the same incremental way, so stderr costs one more
    # HEAD and one more ranged GET — not a second transfer task.
    assert {path for path, _start, _end in globus.range_reads} == {
        f"{ODO}.out/log-44039.out",
        f"{ODO}.out/log-44039.err",
    }


async def test_odo_status_reports_a_missing_stderr_as_nothing_on_stderr(
    monkeypatch, user_cfg, tmp_path
):
    """
    Slurm creates the stderr file only when something writes to it, so a job that
    succeeded leaves none — the fake raises `GlobusFileNotFound` for it, exactly
    as a real collection does. That absence is the answer, not a failure.
    """
    import json

    fixture = Path(__file__).parent / "fixtures" / "iri_status_completed.json"
    iri = FakeIriClient(status=json.loads(fixture.read_text(encoding="utf-8")))
    globus = FakeGlobusClient()
    globus.files[f"{ODO}.out/log-1.out"] = b"TBR = 1.14\n"
    _patch_clients(monkeypatch, iri=iri, globus=globus)

    ran = await _get_job_status(user_cfg, tmp_path, "1", cluster="odo")
    assert "TBR = 1.14" in ran
    assert "(nothing on stderr)" in ran


async def test_odo_status_says_when_stderr_could_not_be_fetched(
    monkeypatch, user_cfg, tmp_path
):
    """
    "We could not look" must never render as "there was nothing there".

    A file that does not exist and a collection that would not answer both leave
    us with nothing locally, and collapsing them would let an outage be read as a
    job that printed no error — the exact mistake this line of work exists to
    stop. Only the first is `(nothing on stderr)`.
    """
    import json

    fixture = Path(__file__).parent / "fixtures" / "iri_status_completed.json"
    iri = FakeIriClient(status=json.loads(fixture.read_text(encoding="utf-8")))
    globus = FakeGlobusClient()
    globus.files[f"{ODO}.out/log-3.out"] = b"setup ok\n"

    real_stat = globus.stat

    async def _refuse(*, collection_id: str, remote_path: str) -> int:
        if remote_path.endswith(".err"):
            raise RuntimeError("endpoint activation expired")
        return await real_stat(collection_id=collection_id, remote_path=remote_path)

    globus.stat = _refuse  # type: ignore[method-assign]
    _patch_clients(monkeypatch, iri=iri, globus=globus)

    text = await _get_job_status(user_cfg, tmp_path, "3", cluster="odo")
    assert "endpoint activation expired" in text
    assert "(nothing on stderr)" not in text, "silence we did not verify is not silence"


async def test_perlmutter_status_formats_golden_fixture(
    monkeypatch, user_cfg, tmp_path
):
    """Status text matches the KEY=VALUE shape campaign parsers expect."""
    fixture = Path(__file__).parent / "fixtures" / "iri_status_completed.json"
    import json

    status = json.loads(fixture.read_text(encoding="utf-8"))
    iri = FakeIriClient(status=status)
    iri.head_content[f"{NERSC}/out/log-pm-1.out"] = "line1\nline2\n"

    monkeypatch.setattr(submit_job_mcp, "client_for", lambda cluster, token: iri)

    text = await _get_job_status(user_cfg, tmp_path, "pm-1", cluster="perlmutter")
    assert "JOB_ID=pm-1" in text
    assert "STATE=COMPLETED" in text
    assert "EXIT_CODE=0" in text
    assert "--- LOGS ---" in text
    assert "line1" in text


async def test_perlmutter_status_shows_stderr(monkeypatch, user_cfg, tmp_path):
    """
    Where a failed job explains itself.

    Reading only stdout is how an argparse usage message — exit status 2, nothing
    on stdout past the setup echoes — reached a debating agent as "no outputs
    recorded", twice.
    """
    fixture = Path(__file__).parent / "fixtures" / "iri_status_completed.json"
    import json

    iri = FakeIriClient(status=json.loads(fixture.read_text(encoding="utf-8")))
    iri.head_content[f"{NERSC}/out/log-pm-2.out"] = "setup ok\n"
    iri.head_content[f"{NERSC}/out/log-pm-2.err"] = (
        "run_state_point.py: error: unrecognized arguments: --salt flibe_90Li6\n"
    )

    monkeypatch.setattr(submit_job_mcp, "client_for", lambda cluster, token: iri)

    text = await _get_job_status(user_cfg, tmp_path, "pm-2", cluster="perlmutter")
    assert "--- STDERR ---" in text
    assert "unrecognized arguments: --salt flibe_90Li6" in text


# ---------------------------------------------------------------- one OLCF submit path


@pytest.mark.parametrize(
    ("cluster", "job", "base"),
    [("odo", "salt-neutronics-tbr", ODO), ("frontier", "forge-tune", FRONTIER)],
)
async def test_olcf_spec_is_the_iri_jobspec_with_setup_in_the_body(
    monkeypatch, user_cfg, cluster, job, base
):
    """
    The resource is the IRI path's, never an attribute, and the setup script
    runs inside the job after the shared prefix rather than as `pre_launch`.
    """
    iri = FakeIriClient(job_id="9")
    globus = FakeGlobusClient()
    globus.seed_remote_dir(base)
    _patch_clients(monkeypatch, iri=iri, globus=globus)

    await _submit_job(cluster, user_cfg, job, None, None, "--flag x")

    [(spec, name)] = iri.submitted
    assert name == f"vista-{job}"
    assert "resource_id" not in spec["attributes"]
    assert "pre_launch" not in spec and "pre_launch" not in spec["attributes"]
    assert set(spec["attributes"]) <= {
        "queue_name",
        "account",
        "duration",
        "custom_attributes",
    }
    # What the client sends is what was built: nothing is left to lift out.
    from vista_mcp_server.lib.iri import wire_job_spec

    wire = wire_job_spec(spec, name=name)
    for key in ("directory", "stdout_path", "stderr_path", "environment", "attributes"):
        assert wire[key] == spec[key]

    body = spec["arguments"][2]
    setup = (HPC_JOBS_DIR / job / f"setup_{cluster}.sh").read_text(encoding="utf-8")
    script = (HPC_JOBS_DIR / job / f"job.{cluster}.slurm").read_text(encoding="utf-8")
    order = [
        body.index("export VISTA_OUT="),
        body.index("module purge"),
        body.index(f"bash -lc {shlex.quote(setup)}"),
        body.index("set -- --flag x"),
        body.index(script),
    ]
    assert order == sorted(order)
    assert f"VISTA: setup_{cluster}.sh failed with exit code" in body
    if cluster == "odo":  # Odo's job scripts run from their sources
        assert body.index("cd ") < body.index("bash -lc")


def _run(body: str, home: Path) -> subprocess.CompletedProcess:
    """`body` under bash, with a HOME that has no login profile to read."""
    return subprocess.run(
        ["bash", "-c", body],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={"PATH": os.environ["PATH"], "HOME": str(home)},
    )


needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@needs_bash
def test_setup_sees_the_prefix_and_the_job_gets_its_arguments(tmp_path):
    body = _job_body(
        'export VISTA_OUT=/scratch/out/7\nexport https_proxy="http://proxy:3128"',
        'echo "job $1|$2|$VISTA_OUT"',
        setup=("setup_odo.sh", 'echo "setup $VISTA_OUT $https_proxy"; export LEAK=1'),
        script_args="a 'b c'",
    )
    ran = _run(body, tmp_path)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout.splitlines() == [
        "setup /scratch/out/7 http://proxy:3128",
        "job a|b c|/scratch/out/7",
    ]


@needs_bash
def test_a_failing_setup_ends_the_job_with_its_exit_code(tmp_path):
    body = _job_body(
        "export VISTA_OUT=/scratch/out/7",
        "echo job-ran",
        setup=("setup_frontier.sh", "echo 'missing input' >&2; exit 3"),
    )
    ran = _run(body, tmp_path)
    assert ran.returncode == 3
    assert "job-ran" not in ran.stdout
    assert "missing input" in ran.stderr
    assert "VISTA: setup_frontier.sh failed with exit code 3" in ran.stderr


@needs_bash
def test_setup_exports_stay_out_of_the_job_as_before(tmp_path):
    body = _job_body(
        ":", 'echo "leak=${LEAK:-none}"', setup=("setup_odo.sh", "export LEAK=1")
    )
    assert _run(body, tmp_path).stdout.strip() == "leak=none"
