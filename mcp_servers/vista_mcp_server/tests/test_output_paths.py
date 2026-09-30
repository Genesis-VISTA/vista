"""
Job-output downloads must land inside the job's output directory on every host, and the
paths reported to the agent are sandbox paths, so POSIX whatever the host is.
"""

from pathlib import Path, PurePosixPath

import pytest

from vista_mcp_server.submit_job_mcp import SANDBOX_OUTPUT_DIR, _job_output_paths

pytestmark = pytest.mark.unit

SANDBOX_JOB_DIR = SANDBOX_OUTPUT_DIR / "12345"


@pytest.mark.parametrize(
    "name",
    [
        "/etc/x",
        "C:\\x",
        "C:x",
        "\\\\host\\share\\x",
        "\\x",
        "../x",
        "..\\x",
        "results/../../x",
        "results\\plot.png",
        "",
    ],
)
def test_absolute_or_escaping_names_are_rejected(tmp_path: Path, name: str):
    with pytest.raises(ValueError, match="Invalid path"):
        _job_output_paths(tmp_path / "12345", SANDBOX_JOB_DIR, name)


def test_a_symlink_out_of_the_output_dir_is_rejected(tmp_path: Path):
    out_dir = tmp_path / "12345"
    out_dir.mkdir()
    (out_dir / "escape").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="Invalid path"):
        _job_output_paths(out_dir, SANDBOX_JOB_DIR, "escape/secret.txt")


def test_relative_names_map_inside_both_dirs(tmp_path: Path):
    out_dir = tmp_path / "12345"
    local, sandbox = _job_output_paths(out_dir, SANDBOX_JOB_DIR, "results/summary.csv")
    assert local == out_dir / "results" / "summary.csv"
    assert sandbox == PurePosixPath("/mnt/data/output/12345/results/summary.csv")


def test_reported_sandbox_paths_use_forward_slashes(tmp_path: Path):
    _, sandbox = _job_output_paths(tmp_path, SANDBOX_JOB_DIR, "a/b/c.txt")
    assert str(sandbox) == "/mnt/data/output/12345/a/b/c.txt"
    assert "\\" not in str(sandbox)
