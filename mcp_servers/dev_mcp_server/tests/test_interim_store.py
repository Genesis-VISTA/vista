"""
TEMPORARY(windows-support): remove before merge, together with the default in config.py.

Until the microsandbox 0.7 upgrade merges, the dev server must not open the shared
~/.microsandbox, which 0.7 would migrate out from under checkouts still on 0.5.7.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_PROBE = "import os, dev_mcp_server.server; print(os.environ['MSB_HOME'])"


def _msb_home(tmp_path: Path, **env: str) -> str:
    base = {k: v for k, v in os.environ.items() if k != "MSB_HOME"}
    base["HOME"] = str(tmp_path)
    base.update(env)
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        env=base,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return proc.stdout.strip().splitlines()[-1]


def test_defaults_to_the_interim_store(tmp_path: Path):
    assert _msb_home(tmp_path) == str(tmp_path / ".microsandbox-interim")


def test_explicit_msb_home_wins(tmp_path: Path):
    assert _msb_home(tmp_path, MSB_HOME=str(tmp_path / "mine")) == str(
        tmp_path / "mine"
    )
