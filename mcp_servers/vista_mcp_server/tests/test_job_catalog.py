"""Contract tests for the curated ``hpc_jobs/`` catalog."""

from __future__ import annotations

from pathlib import Path

import pytest

from vista_mcp_server.config import settings
from vista_mcp_server.submit_job_mcp import (
    FRONTIER_JOB_SCRIPT,
    LUX_JOB_SCRIPT,
    ODO_JOB_SCRIPT,
    PERLMUTTER_JOB_SCRIPT,
    ClusterDefaults,
    get_available_jobs,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
HPC_JOBS_DIR = REPO_ROOT / "hpc_jobs"
CLUSTER_SCRIPTS = (
    ODO_JOB_SCRIPT,
    PERLMUTTER_JOB_SCRIPT,
    FRONTIER_JOB_SCRIPT,
    LUX_JOB_SCRIPT,
)

pytestmark = pytest.mark.unit


def _job_dirs() -> list[Path]:
    return sorted(
        p for p in HPC_JOBS_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")
    )


@pytest.fixture
def point_at_repo_jobs(monkeypatch):
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", HPC_JOBS_DIR)


@pytest.mark.parametrize("job_dir", _job_dirs(), ids=lambda p: p.name)
def test_on_disk_jobs_meet_contract(job_dir: Path):
    readme = job_dir / "README.md"
    assert readme.is_file(), f"{job_dir.name}: missing README.md"
    text = readme.read_text(encoding="utf-8").strip()
    assert text.startswith(f"# {job_dir.name}"), (
        f'{job_dir.name}: README.md must start with "# {job_dir.name}"'
    )
    assert any((job_dir / s).exists() for s in CLUSTER_SCRIPTS), (
        f"{job_dir.name}: need at least one of {CLUSTER_SCRIPTS}"
    )
    defaults_path = job_dir / "cluster_defaults.json"
    if defaults_path.exists():
        ClusterDefaults.model_validate_json(defaults_path.read_text(encoding="utf-8"))


def test_get_available_jobs_matches_disk(point_at_repo_jobs):
    jobs = get_available_jobs()
    on_disk = {p.name for p in _job_dirs()}
    assert set(jobs) == on_disk
    for name, info in jobs.items():
        assert info.name == name
        assert info.description.startswith(f"# {name}")


def test_missing_readme_raises(tmp_path, monkeypatch):
    job = tmp_path / "broken-job"
    job.mkdir()
    (job / ODO_JOB_SCRIPT).write_text("#!/bin/bash\necho hi\n", encoding="utf-8")
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", tmp_path)
    with pytest.raises(ValueError, match="No README.md"):
        get_available_jobs()


def test_no_job_script_raises(tmp_path, monkeypatch):
    job = tmp_path / "no-script"
    job.mkdir()
    (job / "README.md").write_text("# no-script\n\nDesc.\n", encoding="utf-8")
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", tmp_path)
    with pytest.raises(ValueError, match="no job script"):
        get_available_jobs()


def test_bad_readme_header_raises(tmp_path, monkeypatch):
    job = tmp_path / "bad-header"
    job.mkdir()
    (job / ODO_JOB_SCRIPT).write_text("#!/bin/bash\n", encoding="utf-8")
    (job / "README.md").write_text("# wrong-name\n\nDesc.\n", encoding="utf-8")
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", tmp_path)
    with pytest.raises(ValueError, match="should start with"):
        get_available_jobs()
