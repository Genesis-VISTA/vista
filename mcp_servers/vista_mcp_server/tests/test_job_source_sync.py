"""Job sources must be re-uploaded when they change on disk."""
import pytest
from pathlib import Path
from vista_mcp_server import submit_job_mcp as m

pytestmark = pytest.mark.unit


class _FakeGlobus:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.transfers = []
        self.mkdirs = []

    async def operation_ls(self, *, endpoint, path, **kw):
        return self.existing

    async def operation_mkdir_p(self, *, endpoint, path, parents_below):
        self.mkdirs.append(path)

    async def transfer_and_wait(self, *, src_endpoint, dst_endpoint, items, label, **kw):
        self.transfers.append({"items": items, "label": label, "kw": kw})
        return {"status": "SUCCEEDED"}


@pytest.fixture
def job_dir(tmp_path, monkeypatch):
    d = tmp_path / "hpc_jobs" / "myjob"
    d.mkdir(parents=True)
    (d / "run_stage.py").write_text("print('v1')\n")
    (d / "README.md").write_text("# myjob\n")
    (d / "cluster_defaults.json").write_text("{}\n")
    monkeypatch.setattr(m.settings, "local_hpc_jobs_dir", tmp_path / "hpc_jobs")
    return d


@pytest.mark.anyio
async def test_sources_are_uploaded_even_when_the_remote_dir_is_populated(job_dir):
    """
    The regression: the upload used to be skipped whenever src_dir had entries, so
    editing a job wrapper locally and resubmitting kept running the OLD code on the
    cluster, silently.
    """
    g = _FakeGlobus(existing=[{"name": "run_stage.py", "type": "file"}])
    await m._sync_job_sources(g, "myjob", "/base/myjob/src", base="/base",
                              remote_endpoint="ep")
    assert g.transfers, "populated src_dir must not skip the upload"
    names = {Path(src).name for src, _dst, _r in g.transfers[0]["items"]}
    assert "run_stage.py" in names


@pytest.mark.anyio
async def test_upload_uses_checksum_sync_so_unchanged_files_are_free(job_dir):
    g = _FakeGlobus()
    await m._sync_job_sources(g, "myjob", "/base/myjob/src", base="/base",
                              remote_endpoint="ep")
    assert g.transfers[0]["kw"].get("sync_level") == "checksum"


@pytest.mark.anyio
async def test_orchestration_metadata_is_not_uploaded(job_dir):
    g = _FakeGlobus()
    await m._sync_job_sources(g, "myjob", "/base/myjob/src", base="/base",
                              remote_endpoint="ep")
    names = {Path(src).name for src, _dst, _r in g.transfers[0]["items"]}
    assert "run_stage.py" in names
    assert "cluster_defaults.json" not in names


@pytest.fixture
def anyio_backend():
    return "asyncio"
