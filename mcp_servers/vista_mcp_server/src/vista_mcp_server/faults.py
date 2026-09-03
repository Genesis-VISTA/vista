"""
Fault injection for HPC tools (evaluation plan M7).

Probabilistic submit failures, status-poll timeouts, and simulated token
expiry, gated by `VISTA_MCP_FAULT__*` (see `config.FaultSettings`). Inert
by default and refused in prod by the startup guard, so this is a no-op on
any normal deployment. Drives E7a: replay the loadgen campaigns under a
fault profile and measure auto-recovery rate by fault type.

Faults raise `ToolError`, surfacing to the agent as a tool-call error
(the same shape a real IRI/S3 failure takes), so the recovery path
under test is the real one.
"""

from __future__ import annotations

import random
import time

from fastmcp.exceptions import ToolError

from .config import settings

# Dedicated RNG so fault decisions are independent of any other use of the
# global `random` module; tests seed it for determinism.
_rng = random.Random()

# Monotonic time of the first HPC call in this process, for token-expiry
# simulation. Set lazily on the first `check_token_expiry()`.
_first_hpc_call: float | None = None


def seed(value: int) -> None:
    """Seed the fault RNG (tests / reproducible fault profiles)."""
    _rng.seed(value)


def reset() -> None:
    """Clear process-global fault state (tests)."""
    global _first_hpc_call
    _first_hpc_call = None


def maybe_fail_submit() -> None:
    """Raise a synthetic submit failure with probability `submit_fail_p`."""
    p = settings.fault.submit_fail_p
    if p > 0.0 and _rng.random() < p:
        raise ToolError(
            "VISTA_MCP_FAULT: synthetic job submission failure (injected). "
            "Retry the submission."
        )


def maybe_timeout_status() -> None:
    """Raise a synthetic status-poll timeout with probability `status_timeout_p`."""
    p = settings.fault.status_timeout_p
    if p > 0.0 and _rng.random() < p:
        raise ToolError(
            "VISTA_MCP_FAULT: synthetic status-poll timeout (injected). "
            "Retry the status check."
        )


def check_token_expiry() -> None:
    """
    Raise a simulated token-expiry error once `token_expire_after_s` have
    elapsed since the first HPC call in this process. Models S3M's 24 h
    token lifetime so the cross-expiry recovery path (re-auth, resume) can
    be exercised in a short test (set the window to seconds).
    """
    window = settings.fault.token_expire_after_s
    if window <= 0.0:
        return
    global _first_hpc_call
    now = time.monotonic()
    if _first_hpc_call is None:
        _first_hpc_call = now
        return
    if now - _first_hpc_call > window:
        raise ToolError(
            "VISTA_MCP_FAULT: simulated token expiry (injected). The HPC "
            "credential has expired; re-authenticate and resume the campaign."
        )
