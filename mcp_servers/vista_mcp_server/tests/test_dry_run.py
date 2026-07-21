"""
Tests for the HPC dry-run (evaluation plan M6): synthetic submit/status
state machine and the queue-delay behavior that powers E7b. No real
cluster contact; wall-clock is driven through a patched `time.monotonic`.
"""

import pytest

import vista_mcp_server.dry_run as dry_run
from vista_mcp_server.config import settings


@pytest.fixture(autouse=True)
def clean_registry():
    dry_run.reset()
    yield
    dry_run.reset()


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(dry_run.time, "monotonic", lambda: now["t"])

    def advance(seconds: float):
        now["t"] += seconds

    return advance


def test_enabled_reflects_setting(monkeypatch):
    monkeypatch.setattr(settings, "hpc_dry_run", False)
    assert dry_run.enabled() is False
    monkeypatch.setattr(settings, "hpc_dry_run", True)
    assert dry_run.enabled() is True


def test_submit_returns_dry_job_id(monkeypatch, clock):
    monkeypatch.setattr(settings, "hpc_queue_delay_s", 0.0)
    job_id = dry_run.record_submit("odo", "example", nodes=2, duration_s=600)
    assert job_id.startswith("dry-")
    assert dry_run.is_dry_job(job_id)
    # validate_job_id's charset ([\w-]+) must accept it.
    import re

    assert re.fullmatch(r"[\w-]+", job_id)


def test_completes_immediately_with_no_queue_delay(monkeypatch, clock):
    monkeypatch.setattr(settings, "hpc_queue_delay_s", 0.0)
    job_id = dry_run.record_submit("odo", "example", 1, 3600)
    assert dry_run.state(job_id) == "COMPLETED"
    text = dry_run.status_text(job_id)
    assert "STATE=COMPLETED" in text and "EXIT_CODE=0" in text
    assert "--- LOGS ---" in text


def test_queue_delay_pending_then_completed(monkeypatch, clock):
    monkeypatch.setattr(settings, "hpc_queue_delay_s", 300.0)  # E7b: 5 min
    job_id = dry_run.record_submit("odo", "example", 1, 3600)

    assert dry_run.state(job_id) == "PENDING"
    assert "STATE=PENDING" in dry_run.status_text(job_id)
    assert "EXIT_CODE" not in dry_run.status_text(job_id)

    clock(299)
    assert dry_run.state(job_id) == "PENDING"
    clock(2)  # past 300s
    assert dry_run.state(job_id) == "COMPLETED"


def test_per_job_queue_delay_snapshot(monkeypatch, clock):
    """A job snapshots the delay at submit, so two jobs from runs with
    different delays coexist correctly."""
    monkeypatch.setattr(settings, "hpc_queue_delay_s", 10.0)
    slow = dry_run.record_submit("odo", "example", 1, 3600)
    monkeypatch.setattr(settings, "hpc_queue_delay_s", 0.0)
    fast = dry_run.record_submit("odo", "example", 1, 3600)

    assert dry_run.state(fast) == "COMPLETED"
    assert dry_run.state(slow) == "PENDING"


def test_cancel_all_clusters(monkeypatch, clock):
    monkeypatch.setattr(settings, "hpc_queue_delay_s", 300.0)
    for cluster in ("odo", "frontier", "perlmutter"):
        dry_run.reset()
        job_id = dry_run.record_submit(cluster, "example", 1, 3600)
        dry_run.cancel(job_id)
        assert dry_run.state(job_id) == "CANCELLED"
        assert "STATE=CANCELLED" in dry_run.status_text(job_id)


def test_submit_perlmutter_and_frontier_complete(monkeypatch, clock):
    monkeypatch.setattr(settings, "hpc_queue_delay_s", 0.0)
    for cluster in ("perlmutter", "frontier"):
        job_id = dry_run.record_submit(cluster, "example", nodes=1, duration_s=600)
        assert job_id.startswith("dry-")
        assert dry_run.state(job_id) == "COMPLETED"
        text = dry_run.status_text(job_id)
        assert "STATE=COMPLETED" in text
        assert f"CLUSTER={cluster}" in text
        assert "EXIT_CODE=0" in text


def test_config_env(monkeypatch):
    monkeypatch.setenv("VISTA_MCP_HPC_DRY_RUN", "true")
    monkeypatch.setenv("VISTA_MCP_HPC_QUEUE_DELAY_S", "3600")
    from vista_mcp_server.config import AppSettings

    s = AppSettings()
    assert s.hpc_dry_run is True
    assert s.hpc_queue_delay_s == 3600.0
