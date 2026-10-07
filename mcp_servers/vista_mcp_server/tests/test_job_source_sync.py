"""
Job sources must be re-uploaded when they change on disk.

The regression: the upload was skipped whenever `src_dir` had ANY entries, which pinned
the cluster to whatever version of a job's scripts was staged first — editing
`hpc_jobs/<job>/run_*.py` locally and resubmitting kept running the old code, silently.
Sources now go one HTTPS PUT per file, and the skip is per-file on name AND size: an
interrupted PUT leaves a file with the right name and the wrong length, so name alone
would reproduce the same bug one level down.
"""

import pytest

from vista_mcp_server import submit_job_mcp as m

pytestmark = pytest.mark.unit


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _FakeGlobus:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.uploaded: list[str] = []
        self.mkdirs: list[str] = []

    async def operation_ls(self, *, endpoint, path, **kw):
        return self.existing

    async def operation_mkdir_p(self, *, endpoint, path, parents_below):
        self.mkdirs.append(path)

    async def upload_file(self, *, collection_id, local_path, remote_path):
        self.uploaded.append(remote_path.rsplit("/", 1)[-1])


@pytest.fixture
def job_dir(tmp_path, monkeypatch):
    d = tmp_path / "hpc_jobs" / "myjob"
    d.mkdir(parents=True)
    (d / "run_stage.py").write_text("print('v1')\n", encoding="utf-8")
    (d / "README.md").write_text("# myjob\n", encoding="utf-8")
    (d / "cluster_defaults.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(m.settings, "local_hpc_jobs_dir", tmp_path / "hpc_jobs")
    return d


def _entry(name, size):
    return {"name": name, "type": "file", "size": size}


async def _sync(g):
    await m._sync_job_sources(
        g, "myjob", "/p/base.jobs/myjob/src", parents_below="/p", remote_endpoint="ep"
    )


@pytest.mark.anyio
async def test_uploads_everything_when_the_remote_dir_is_empty(job_dir):
    g = _FakeGlobus()
    await _sync(g)
    assert "run_stage.py" in g.uploaded


@pytest.mark.anyio
async def test_a_changed_source_is_re_uploaded(job_dir):
    """The regression itself: a stale remote copy must not be left in place."""
    stale_size = (job_dir / "run_stage.py").stat().st_size + 10
    g = _FakeGlobus(
        existing=[_entry("run_stage.py", stale_size), _entry("README.md", 999)]
    )
    await _sync(g)
    assert "run_stage.py" in g.uploaded


@pytest.mark.anyio
async def test_a_truncated_upload_is_detected_by_size_not_name(job_dir):
    """An interrupted PUT keeps the name; only the length gives it away."""
    g = _FakeGlobus(existing=[_entry("run_stage.py", 1)])
    await _sync(g)
    assert "run_stage.py" in g.uploaded


@pytest.mark.anyio
async def test_an_identical_tree_is_not_re_sent(job_dir):
    sources = {
        f.name: f.stat().st_size
        for f in job_dir.iterdir()
        if f.is_file() and f.name not in m._HPC_JOB_METADATA_FILES
    }
    g = _FakeGlobus(existing=[_entry(n, s) for n, s in sources.items()])
    await _sync(g)
    assert g.uploaded == [], "an unchanged tree should cost no uploads"


@pytest.mark.anyio
async def test_orchestration_metadata_is_not_uploaded(job_dir):
    g = _FakeGlobus()
    await _sync(g)
    assert "run_stage.py" in g.uploaded
    assert "cluster_defaults.json" not in g.uploaded
