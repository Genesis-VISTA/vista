"""
MCP tool implementation for running the salt-analysis skill script safely.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any


_SALT_RE = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")


def run_salt_analysis(*, salt: str, data_path: str | None = None) -> dict[str, Any]:
    """
    Run the salt-analysis skill script for a given salt name.

    Security properties:
    - Executes a fixed script path (no arbitrary commands)
    - Validates salt input format
    - Restricts working directory to the skill directory
    - Captures stdout/stderr
    """
    salt = salt.strip()
    if not _SALT_RE.match(salt):
        raise ValueError(
            "Invalid salt format. Use alphanumerics separated by hyphens, e.g. AlCl3-KCl."
        )

    # repo_root/.../src/project_name/tools/salt_analysis.py -> repo_root is parents[3]
    repo_root = Path(__file__).resolve().parents[3]
    skill_dir = repo_root / "skills" / "salt-analysis"
    script = skill_dir / "scripts" / "analyze_salt.py"

    if not script.exists():
        raise FileNotFoundError(f"Missing skill script: {script}")

    # Default dataset inside the skill
    if data_path is None:
        data_path = str(skill_dir / "assets" / "Molten_Salt_Thermophysical_Properties.json")

    cmd = [sys.executable, str(script), "--salt", salt, "--data", data_path]

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(skill_dir),
        check=False,
    )

    # Try to extract a plot path from stdout (optional; your script prints it)
    plot_path: str | None = None
    marker = "Plot saved to "
    for line in proc.stdout.splitlines():
        if marker in line:
            plot_path = line.split(marker, 1)[1].strip()
            break

    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "plot_path": plot_path,
    }