"""Getting a job's sources onto the cluster, one HTTPS PUT at a time.

A single Globus transfer task used to move the whole set, which made the
idempotence question easy: the directory was either untouched or complete. One
PUT per file is not atomic, so the interesting cases are all about what happens
after a submission that stopped part way -- and the answer has to be that the
next one finishes the job, not that it declares the directory populated and
launches against half a source tree. A PUT can stop part way through a single
file as well as between two of them, so "already there" is a question about
lengths, not names.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vista_mcp_server.config import settings
from vista_mcp_server.lib.globus import GlobusSessionExpired
from vista_mcp_server.submit_job_mcp import _HPC_JOB_METADATA_FILES, _sync_job_sources
from fakes import FakeGlobusClient

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

COLLECTION = "odo-collection"
BASE = "/gpfs/vista"
SRC = f"{BASE}/demo/src"


@pytest.fixture
def job_dir(monkeypatch, tmp_path) -> Path:
    """A job with two real sources and one orchestration file that never goes."""
    jobs = tmp_path / "hpc_jobs"
    demo = jobs / "demo"
    demo.mkdir(parents=True)
    (demo / "run.py").write_text("print('run')\n")
    (demo / "helper.py").write_text("print('helper')\n")
    (demo / "README.md").write_text("# demo\n")
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", jobs)
    return demo


async def sync(globus: FakeGlobusClient) -> None:
    await _sync_job_sources(globus, "demo", SRC, base=BASE, remote_endpoint=COLLECTION)


def uploaded(globus: FakeGlobusClient) -> set[str]:
    return {path.rsplit("/", 1)[-1] for _, path in globus.uploads}


async def test_a_first_submission_uploads_every_source(job_dir):
    globus = FakeGlobusClient()

    await sync(globus)

    assert uploaded(globus) == {"run.py", "helper.py"}
    assert globus.mkdir_p_calls == [(COLLECTION, SRC, BASE)]


async def test_orchestration_metadata_never_goes_to_the_cluster(job_dir):
    """`README.md` and the cluster-defaults files drive submission here; the
    cluster has no use for them and `RUN_DIR` is the job's own directory."""
    globus = FakeGlobusClient()

    await sync(globus)

    assert "README.md" in _HPC_JOB_METADATA_FILES
    assert not any(name in _HPC_JOB_METADATA_FILES for name in uploaded(globus))


def remote(
    job_dir: Path, *names: str, short: dict[str, int] | None = None
) -> list[dict]:
    """An `operation_ls` result for sources already on the collection.

    Sizes are the local files' own, since that is what a completed upload
    leaves; `short` overrides one to model a `PUT` that stopped part way.
    """
    short = short or {}
    return [
        {
            "name": name,
            "type": "file",
            "size": short.get(name, (job_dir / name).stat().st_size),
        }
        for name in names
    ]


async def test_a_complete_source_dir_is_left_alone(job_dir):
    globus = FakeGlobusClient()
    globus.ls_entries[SRC] = remote(job_dir, "run.py", "helper.py")

    await sync(globus)

    assert globus.uploads == []


async def test_a_half_uploaded_source_dir_is_finished_rather_than_skipped(job_dir):
    """The failure this design has and the transfer task did not: a submission
    that died after one PUT. Asking only whether the directory has *entries*
    would call this done and launch the job against a missing file, which fails
    on the cluster saying nothing about why."""
    globus = FakeGlobusClient()
    globus.ls_entries[SRC] = remote(job_dir, "run.py")

    await sync(globus)

    assert uploaded(globus) == {"helper.py"}


async def test_a_source_left_truncated_by_a_dead_put_is_sent_again(job_dir):
    """One level down from the test above, and the reason name alone will not
    do. A PUT that died mid-body leaves a file with the right NAME and the
    wrong length; accepting it runs the job against a source file that stops
    in the middle, which on the cluster is a syntax error pointing nowhere near
    the cause. The client that died never saw a response, so nothing but this
    probe is left to notice."""
    globus = FakeGlobusClient()
    globus.ls_entries[SRC] = remote(job_dir, "run.py", "helper.py", short={"run.py": 4})

    await sync(globus)

    assert uploaded(globus) == {"run.py"}


async def test_an_entry_with_no_size_is_sent_again(job_dir):
    """Transfer reports a size for every file, so this is a shape nothing real
    produces. Re-sending is the harmless reading of it; skipping would trust a
    length that was never stated."""
    globus = FakeGlobusClient()
    globus.ls_entries[SRC] = [
        {"name": "run.py", "type": "file"},
        *remote(job_dir, "helper.py"),
    ]

    await sync(globus)

    assert uploaded(globus) == {"run.py"}


async def test_a_directory_sharing_a_sources_name_is_not_mistaken_for_it(job_dir):
    """`size` on a dir entry is its own, and comparing it against the file's
    would be an accident either way round."""
    globus = FakeGlobusClient()
    globus.ls_entries[SRC] = [
        {"name": "run.py", "type": "dir", "size": (job_dir / "run.py").stat().st_size},
        *remote(job_dir, "helper.py"),
    ]

    await sync(globus)

    assert uploaded(globus) == {"run.py"}


async def test_an_expired_session_is_not_read_as_an_absent_directory(job_dir):
    """The probe treats most failures as "not there yet, upload it". An expired
    credential is not that, and falling through would rediscover it one PUT at a
    time and report it as a failed upload."""

    class Expired(FakeGlobusClient):
        async def operation_ls(self, **kwargs):
            raise GlobusSessionExpired("reconnect Globus for Odo")

    globus = Expired()

    with pytest.raises(GlobusSessionExpired):
        await sync(globus)
    assert globus.uploads == []


async def test_a_job_with_only_metadata_uploads_nothing(monkeypatch, tmp_path):
    jobs = tmp_path / "hpc_jobs"
    (jobs / "demo").mkdir(parents=True)
    (jobs / "demo" / "README.md").write_text("# demo\n")
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", jobs)
    globus = FakeGlobusClient()

    await sync(globus)

    assert globus.uploads == []
    # And no directory is made for a set of files that was never going to move.
    assert globus.mkdir_p_calls == []
