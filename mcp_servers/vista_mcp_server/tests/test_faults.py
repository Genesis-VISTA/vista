"""
Tests for fault injection (evaluation plan M7) and the prod startup guard
(§2a Group 3): probabilistic submit/status faults, simulated token expiry,
the inert-by-default contract, and that experiment flags are refused when
VISTA_ENV=prod.
"""
import pytest
from fastmcp.exceptions import ToolError

import vista_mcp_server.faults as faults
from vista_mcp_server.config import AppSettings, FaultSettings, settings


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    faults.reset()
    faults.seed(0)
    # Default: all faults inert.
    monkeypatch.setattr(settings, "fault", FaultSettings())
    yield
    faults.reset()


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(faults.time, "monotonic", lambda: now["t"])
    return lambda secs: now.__setitem__("t", now["t"] + secs)


# ---------------------------------------------------------------------------
# Inert by default
# ---------------------------------------------------------------------------

def test_no_faults_by_default():
    for _ in range(100):
        faults.maybe_fail_submit()
        faults.maybe_timeout_status()
        faults.check_token_expiry()  # no raise


# ---------------------------------------------------------------------------
# Probabilistic faults
# ---------------------------------------------------------------------------

def test_submit_fail_certain(monkeypatch):
    monkeypatch.setattr(settings, "fault", FaultSettings(submit_fail_p=1.0))
    with pytest.raises(ToolError, match="submission failure"):
        faults.maybe_fail_submit()


def test_status_timeout_certain(monkeypatch):
    monkeypatch.setattr(settings, "fault", FaultSettings(status_timeout_p=1.0))
    with pytest.raises(ToolError, match="timeout"):
        faults.maybe_timeout_status()


def test_probability_rate_is_respected(monkeypatch):
    """A p=0.3 profile fails roughly 30% of the time (seeded, so stable)."""
    monkeypatch.setattr(settings, "fault", FaultSettings(submit_fail_p=0.3))
    faults.seed(42)
    failures = 0
    for _ in range(1000):
        try:
            faults.maybe_fail_submit()
        except ToolError:
            failures += 1
    assert 250 <= failures <= 350


# ---------------------------------------------------------------------------
# Token expiry
# ---------------------------------------------------------------------------

def test_token_expiry_after_window(monkeypatch, clock):
    monkeypatch.setattr(settings, "fault", FaultSettings(token_expire_after_s=10.0))
    faults.check_token_expiry()  # first call: starts the clock, no raise
    clock(5)
    faults.check_token_expiry()  # still inside window
    clock(6)  # now 11s elapsed, past the 10s window
    with pytest.raises(ToolError, match="token expiry"):
        faults.check_token_expiry()


def test_token_expiry_disabled_when_zero(monkeypatch, clock):
    monkeypatch.setattr(settings, "fault", FaultSettings(token_expire_after_s=0.0))
    faults.check_token_expiry()
    clock(10_000)
    faults.check_token_expiry()  # never expires


# ---------------------------------------------------------------------------
# Config + startup guard
# ---------------------------------------------------------------------------

def test_fault_env_parsing(monkeypatch):
    monkeypatch.setenv("VISTA_MCP_FAULT__SUBMIT_FAIL_P", "0.5")
    monkeypatch.setenv("VISTA_MCP_FAULT__TOKEN_EXPIRE_AFTER_S", "86400")
    s = AppSettings()
    assert s.fault.submit_fail_p == 0.5
    assert s.fault.token_expire_after_s == 86400.0
    assert s.fault.is_active()


def test_guard_allows_experiment_flags_in_dev(monkeypatch):
    # env defaults to 'dev'; experiment flags are fine there.
    monkeypatch.setenv("VISTA_MCP_HPC_DRY_RUN", "true")
    monkeypatch.setenv("VISTA_MCP_FAULT__SUBMIT_FAIL_P", "1.0")
    AppSettings().assert_experiment_flags_allowed()  # no raise


@pytest.mark.parametrize("env_vars,expected", [
    ({"VISTA_MCP_HPC_DRY_RUN": "true"}, "VISTA_MCP_HPC_DRY_RUN"),
    ({"VISTA_MCP_HPC_QUEUE_DELAY_S": "300"}, "VISTA_MCP_HPC_QUEUE_DELAY_S"),
    ({"VISTA_MCP_FAULT__STATUS_TIMEOUT_P": "0.1"}, "VISTA_MCP_FAULT__"),
])
def test_guard_refuses_experiment_flags_in_prod(monkeypatch, env_vars, expected):
    monkeypatch.setenv("VISTA_ENV", "prod")
    for k, v in env_vars.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(RuntimeError, match=expected):
        AppSettings().assert_experiment_flags_allowed()


def test_guard_allows_clean_prod(monkeypatch):
    monkeypatch.setenv("VISTA_ENV", "prod")
    AppSettings().assert_experiment_flags_allowed()  # no raise
