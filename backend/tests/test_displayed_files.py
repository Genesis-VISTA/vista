"""
Unit cases for the displayed-file record's pure parts: the sandbox-path mapper (mirroring
`mcp_servers/vista_mcp_server/tests/test_display_file.py`, since the backend cannot import
that package) and the change status.
"""

import os

import pytest

from vista_backend.services.displayed_files import file_status, sandbox_path_to_file

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "uri, expected",
    [
        ("/mnt/data/output/plot.png", ("outputs", "plot.png")),
        (
            "/mnt/data/output/salt-plots/mstdb_overview.png",
            ("outputs", "salt-plots/mstdb_overview.png"),
        ),
        ("/mnt/data/uploads/input.csv", ("uploads", "input.csv")),
        ("file:///mnt/data/output/plot.png", ("outputs", "plot.png")),
        ("file://localhost/mnt/data/output/plot.png", ("outputs", "plot.png")),
        ("file:///mnt/data/uploads/a/b.csv", ("uploads", "a/b.csv")),
        ("file:///mnt/data/output/my%20plot.png", ("outputs", "my plot.png")),
        ("/mnt/data/output/phase diagram.png", ("outputs", "phase diagram.png")),
        ("/mnt/data/output/fig#1.png", ("outputs", "fig#1.png")),
        ("file:///mnt/data/output/fig%231.png", ("outputs", "fig#1.png")),
    ],
)
def test_maps_the_forms_resolve_uri_accepts(uri, expected):
    assert sandbox_path_to_file(uri) == expected


@pytest.mark.parametrize(
    "uri",
    [
        "/mnt/data/output/../../etc/passwd",
        "file:///mnt/data/output/../../etc/passwd",
        "/mnt/data/output/./plot.png",
        "file:mnt/data/output/plot.png",
        "file://otherhost/mnt/data/output/plot.png",
        "mnt/data/output/plot.png",
        "https://example.com/mnt/data/output/plot.png",
    ],
)
def test_refuses_what_resolve_uri_refuses(uri):
    assert sandbox_path_to_file(uri) is None


@pytest.mark.parametrize(
    "uri",
    [
        "/etc/passwd",
        "/mnt/data/other/plot.png",
        "/mnt/data/output",
        "/mnt/data/output/",
        "/mnt/data/outputs/plot.png",
        "file:///mnt/data/uploads/",
    ],
)
def test_other_prefixes_and_empty_rests_map_to_nothing(uri):
    assert sandbox_path_to_file(uri) is None


def _record(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def test_status_is_current_when_size_and_mtime_match(tmp_path):
    file = tmp_path / "a" / "plot.png"
    file.parent.mkdir()
    file.write_bytes(b"data")
    size, mtime_ns = _record(file)

    assert file_status(tmp_path, "a/plot.png", size, mtime_ns) == "current"


def test_status_is_changed_when_size_or_mtime_differ(tmp_path):
    file = tmp_path / "plot.png"
    file.write_bytes(b"data")
    size, mtime_ns = _record(file)

    assert file_status(tmp_path, "plot.png", size + 1, mtime_ns) == "changed"
    assert file_status(tmp_path, "plot.png", size, mtime_ns + 1) == "changed"
    os.utime(file, ns=(mtime_ns + 5_000, mtime_ns + 5_000))
    assert file_status(tmp_path, "plot.png", size, mtime_ns) == "changed"


def test_status_is_missing_for_a_gone_file_a_directory_or_an_escaping_path(tmp_path):
    files_dir = tmp_path / "output"
    files_dir.mkdir()
    (files_dir / "sub").mkdir()
    (tmp_path / "secret.png").write_bytes(b"x")
    size, mtime_ns = _record(tmp_path / "secret.png")

    assert file_status(files_dir, "gone.png", 1, 1) == "missing"
    assert file_status(files_dir, "sub", 1, 1) == "missing"
    assert file_status(files_dir, "../secret.png", size, mtime_ns) == "missing"
