"""Tests for the persistent HPC job registry in submit_job_mcp.

The registry survives an MCP-server restart so a previously-submitted job stays
pollable (its rendered log/output paths can't be recomputed after the fact).
"""
from vista_mcp_server import submit_job_mcp as m
from vista_mcp_server.submit_job_mcp import (
    SubmittedJob,
    _deserialize_jobs,
    _load_submitted_jobs,
    _persist_submitted_jobs,
    _record_submitted_job,
    _serialize_jobs,
)


def test_serialize_deserialize_round_trip():
    jobs = {
        "123": SubmittedJob(cluster="frontier", log_path="/o/log-123.out", output_dir="/o/123"),
        "456": SubmittedJob(cluster="perlmutter", log_path=None, output_dir=None),
    }
    restored = _deserialize_jobs(_serialize_jobs(jobs))
    assert restored["123"].cluster == "frontier"
    assert restored["123"].log_path == "/o/log-123.out"
    assert restored["123"].output_dir == "/o/123"
    assert restored["456"].cluster == "perlmutter"
    assert restored["456"].log_path is None


def test_persist_then_load_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "reg.json"
    monkeypatch.setattr(
        m,
        "_submitted_jobs",
        {"789": SubmittedJob(cluster="odo", log_path="/o/log-789.out", output_dir="/o/789")},
    )
    _persist_submitted_jobs(path)

    loaded = _load_submitted_jobs(path)
    assert set(loaded) == {"789"}
    assert loaded["789"].cluster == "odo"
    assert loaded["789"].output_dir == "/o/789"


def test_load_missing_file_returns_empty(tmp_path):
    assert _load_submitted_jobs(tmp_path / "does-not-exist.json") == {}


def test_load_corrupt_file_returns_empty(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{ this is not valid json")
    assert _load_submitted_jobs(path) == {}


def test_deserialize_skips_malformed_entries():
    out = _deserialize_jobs(
        {
            "ok": {"cluster": "odo"},
            "missing_cluster": {"log_path": "/x"},
            "not_a_dict": "frontier",
        }
    )
    assert set(out) == {"ok"}
    assert out["ok"].cluster == "odo"
    assert out["ok"].log_path is None


def test_record_updates_memory_and_persists(tmp_path, monkeypatch):
    path = tmp_path / "reg.json"
    monkeypatch.setattr(m, "_submitted_jobs", {})
    monkeypatch.setattr(m, "_registry_path", lambda: path)

    _record_submitted_job(
        "321", SubmittedJob(cluster="frontier", log_path="/o/l", output_dir="/o/d")
    )

    # In-memory registry updated...
    assert "321" in m._submitted_jobs
    assert m._submitted_jobs["321"].cluster == "frontier"
    # ...and durably persisted (as a fresh process would reload it on restart).
    reloaded = _load_submitted_jobs(path)
    assert reloaded["321"].cluster == "frontier"
    assert reloaded["321"].output_dir == "/o/d"
