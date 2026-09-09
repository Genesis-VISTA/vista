"""Hermetic checks that live/hpc marker gates stay wired (Milestone D)."""

from __future__ import annotations

import os

import pytest


@pytest.mark.live
def test_live_marker_requires_env_flag() -> None:
    """Selected only when VISTA_RUN_LIVE=1 (conftest skip + PR marker filter)."""
    assert os.environ.get("VISTA_RUN_LIVE") == "1"


@pytest.mark.hpc
def test_hpc_marker_requires_env_flag() -> None:
    """Selected only when VISTA_RUN_HPC=1 (conftest skip + PR marker filter)."""
    assert os.environ.get("VISTA_RUN_HPC") == "1"
