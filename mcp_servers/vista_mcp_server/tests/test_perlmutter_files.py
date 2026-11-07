"""
Perlmutter's files through IRI filesystem tasks (hpc-job-submission:
"Perlmutter files through IRI filesystem tasks").

The real `IriClient` against `FakeNersc`, which answers in the shapes recorded
from NERSC: so these cover what VISTA makes of a missing log, a failed task, a
task that never finishes and a binary download, through the tools' own paths.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastmcp.exceptions import ToolError

import vista_mcp_server.submit_job_mcp as m
from vista_mcp_server.config import settings
from vista_mcp_server.lib.iri import IriClient, iri_target
from vista_mcp_server.lib.user_config import UserConfig
from fakes.nersc import FakeNersc

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

REPO_ROOT = Path(__file__).resolve().parents[3]
BASE = "/global/homes/r/researcher/vista"
CFG = UserConfig(
    nersc_iri_token="nersc-token", nersc_account="m0000", nersc_remote_dir=BASE
)


async def _no_wait(_seconds: float) -> None:
    pass


@pytest.fixture
def nersc(monkeypatch) -> FakeNersc:
    facility = FakeNersc()
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", REPO_ROOT / "hpc_jobs")

    def client_for(cluster, token):
        assert (cluster, token) == ("perlmutter", "nersc-token")
        return IriClient(
            iri_target(cluster),
            token,
            transport=httpx.MockTransport(facility),
            sleep=_no_wait,
        )

    monkeypatch.setattr(m, "client_for", client_for)
    return facility


async def status(tmp_path: Path, job_id: str = "1001") -> str:
    return await m._get_job_status(CFG, tmp_path, job_id, cluster="perlmutter")


# ---------------------------------------------------------------- submission


async def test_submission_uploads_sources_and_makes_the_log_folder(nersc):
    job_id, log_path, *_ = await m._submit_job(
        "perlmutter", CFG, "forge-tune", None, None, None
    )

    assert (job_id, log_path) == ("1001", f"{BASE}/out/log-1001.out")
    src = f"{BASE}/jobs/forge-tune/src"
    assert f"{src}/job.perlmutter.slurm" not in nersc.files  # inlined, never uploaded
    assert {p for p in nersc.files if p.startswith(src)}  # the sources
    assert f"{BASE}/out" in nersc.dirs
    [body] = nersc.submitted
    assert body["attributes"]["account"] == "m0000"
    assert body["directory"] == f"{BASE}/jobs"


async def test_sources_already_there_are_not_uploaded_again(nersc):
    src = f"{BASE}/jobs/forge-tune/src"
    nersc.files[f"{src}/forge-tune.py"] = b"print('already here')\n"
    nersc._add_parents(f"{src}/forge-tune.py")
    await m._submit_job("perlmutter", CFG, "forge-tune", None, None, None)
    assert [r for r in nersc.requests if "/filesystem/upload/" in r.url.path] == []


async def test_a_failed_upload_names_the_file_and_the_reason_and_submits_nothing(nersc):
    """Scenario: Task fails."""
    target = f"{BASE}/jobs/forge-tune/src/forge-tune.py"
    nersc.fail[("upload", target)] = "Exception: Error: 403: Permission denied"
    with pytest.raises(
        ToolError, match=rf"upload {target} on perlmutter failed: .*Permission denied"
    ):
        await m._submit_job("perlmutter", CFG, "forge-tune", None, None, None)
    assert nersc.submitted == []


async def test_a_sources_check_that_fails_otherwise_stops_rather_than_reuploading(
    nersc,
):
    nersc.fail[("ls", f"{BASE}/jobs/forge-tune/src")] = (
        "Exception: Error: 403: Permission denied"
    )
    with pytest.raises(ToolError, match="Permission denied"):
        await m._submit_job("perlmutter", CFG, "forge-tune", None, None, None)
    assert nersc.submitted == []


# ---------------------------------------------------------------- status


async def test_a_queued_job_is_answered_from_its_state_alone(nersc, tmp_path):
    nersc.job_status = {"state": "queued", "exit_code": 0, "message": None}
    text = await status(tmp_path)
    assert text.splitlines()[:3] == [
        "JOB_ID=1001",
        "CLUSTER=perlmutter",
        "STATE=QUEUED",
    ]
    assert "has not started yet" in text
    assert not [r for r in nersc.requests if "/filesystem/" in r.url.path]


async def test_log_not_written_yet(nersc, tmp_path):
    """Scenario: Log not written yet. A missing log is not an error."""
    text = await status(tmp_path)
    assert "STATE=ACTIVE" in text
    assert "--- LOGS ---\n\n(no logs yet)" in text
    assert "(nothing on stderr)" in text
    assert "(no output files yet)" in text
    assert "unable" not in text


async def test_logs_stderr_and_outputs(nersc, tmp_path):
    nersc.files.update(
        {
            f"{BASE}/out/log-1001.out": b"step 1\nstep 2\n",
            f"{BASE}/out/log-1001.err": b"Traceback: boom\n",
            f"{BASE}/out/1001/result.json": b"{}",
            f"{BASE}/out/1001/plots/loss.png": bytes(range(256)),
            f"{BASE}/out/1001/.venv/lib/x.py": b"",
        }
    )
    for p in list(nersc.files):
        nersc._add_parents(p)
    nersc.job_status = {"state": "failed", "exit_code": 1, "message": "None"}

    text = await status(tmp_path)
    assert "STATE=FAILED\nEXIT_CODE=1\n" in text
    assert "step 2" in text and "Traceback: boom" in text
    assert text.endswith("--- OUTPUT FILES ---\n\nplots/loss.png\nresult.json")


async def test_a_listing_that_never_finishes_is_not_an_empty_folder(nersc, tmp_path):
    """Scenario: Task never finishes."""
    nersc.stall.add("ls")
    text = await status(tmp_path)
    assert "(output listing unfinished:" in text
    assert "(no output files yet)" not in text


# ---------------------------------------------------------------- outputs


async def test_outputs_download_text_and_binary_intact(nersc, tmp_path):
    binary = bytes(range(256)) * 4
    nersc.files.update(
        {
            f"{BASE}/out/1001/model.pt": binary,
            f"{BASE}/out/1001/notes.txt": "héllo\n".encode("utf-8"),
        }
    )
    text = await m._get_iri_job_outputs(
        CFG, tmp_path, "1001", ["model.pt", "notes.txt"], cluster="perlmutter"
    )
    assert text.splitlines()[1:] == [
        "/mnt/data/output/1001/model.pt",
        "/mnt/data/output/1001/notes.txt",
    ]
    assert (tmp_path / "1001" / "model.pt").read_bytes() == binary
    assert (tmp_path / "1001" / "notes.txt").read_text(encoding="utf-8") == "héllo\n"


async def test_outputs_already_held_are_not_fetched_again(nersc, tmp_path):
    (tmp_path / "1001").mkdir()
    (tmp_path / "1001" / "result.json").write_text("{}", encoding="utf-8")
    await m._get_iri_job_outputs(
        CFG, tmp_path, "1001", ["result.json"], cluster="perlmutter"
    )
    assert nersc.requests == []


async def test_a_name_outside_the_output_folder_is_refused_before_any_fetch(
    nersc, tmp_path
):
    with pytest.raises(ValueError, match="Invalid path"):
        await m._get_iri_job_outputs(
            CFG, tmp_path, "1001", ["ok.txt", "../../etc/passwd"], cluster="perlmutter"
        )
    assert nersc.requests == []
