"""
The recorded IRI responses (tests/fixtures/iri) parse and say what their names say.

The IRI client's tests replay these files, so a fixture edited into a shape the
facilities never send would make those tests prove nothing. See the folder's README
for where each came from.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parent / "fixtures" / "iri"
JOB_STATES = {"new", "queued", "held", "active", "completed", "failed", "canceled"}
TASK_STATES = {"pending", "active", "completed", "failed", "canceled"}

JOBS = {
    "olcf_v2/submit.json": ("queued", None),
    "olcf_v2/queued.json": ("queued", 0),
    "olcf_v2/failed_exit7.json": ("failed", 7),
    "olcf_v2/canceled.json": ("canceled", 0),
    "nersc_v1/submit.json": ("queued", None),
    "nersc_v1/queued.json": ("queued", 0),
    "nersc_v1/failed_exit7.json": ("failed", 7),
}
TASKS = {
    "nersc_v1/task_pending.json": "pending",
    "nersc_v1/mkdir.json": "completed",
    "nersc_v1/upload.json": "completed",
    "nersc_v1/ls.json": "completed",
    "nersc_v1/ls_recursive.json": "completed",
    "nersc_v1/head.json": "completed",
    "nersc_v1/stat.json": "completed",
    "nersc_v1/download_text.json": "completed",
    "nersc_v1/download_binary.json": "completed",
    "nersc_v1/head_missing.json": "failed",
    "nersc_v1/ls_missing.json": "failed",
}


def load(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def test_every_fixture_is_checked():
    on_disk = {p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.json")}
    assert on_disk == set(JOBS) | set(TASKS) | {"nersc_v1/task_submitted.json"}


@pytest.mark.parametrize(("rel", "expected"), JOBS.items(), ids=list(JOBS))
def test_job(rel, expected):
    state, exit_code = expected
    job = load(rel)
    assert isinstance(job["id"], str) and job["id"].isdigit()
    status = job["status"]
    assert status["state"] in JOB_STATES
    assert status["state"] == state
    assert status.get("exit_code") == exit_code
    assert isinstance(status.get("time", 0.0), float)


@pytest.mark.parametrize(("rel", "state"), TASKS.items(), ids=list(TASKS))
def test_task(rel, state):
    task = load(rel)
    assert task["status"] in TASK_STATES
    assert task["status"] == state
    assert task["command"]["router"] == "filesystem"
    if state == "failed":
        assert "404" in task["result"]["error"]
    elif state == "completed":
        assert "output" in task["result"]
    else:
        assert task["result"] is None


def test_task_submitted_points_at_its_task():
    sub = load("nersc_v1/task_submitted.json")
    assert sub["task_uri"].endswith(f"/api/v1/task/{sub['task_id']}")
    assert load("nersc_v1/task_pending.json")["id"] == sub["task_id"]


def test_ls_lists_resolved_paths_with_the_directory_first():
    task = load("nersc_v1/ls.json")
    asked = task["command"]["args"]["path"]
    entries = task["result"]["output"]
    assert asked.startswith("/global/homes/")
    assert entries[0]["type"] == "d"
    assert all(e["name"].startswith("/global/u1/") for e in entries)
    assert entries[0]["name"].rsplit("/", 1)[-1] == asked.rsplit("/", 1)[-1]


def test_download_encoding_is_told_apart_by_stat_size():
    size = load("nersc_v1/stat.json")["result"]["output"]["size"]
    binary = load("nersc_v1/download_binary.json")["result"]["output"]
    assert len(base64.b64decode(binary, validate=True)) == size

    text = load("nersc_v1/download_text.json")["result"]["output"]
    listed = {
        e["name"].rsplit("/", 1)[-1]: int(e["size"])
        for e in load("nersc_v1/ls.json")["result"]["output"]
    }
    assert len(text.encode("utf-8")) == listed["hello.txt"]
