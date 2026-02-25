"""
MCP Server to run basic shell commands
"""

from __future__ import annotations
from typing import Annotated as A
from fastmcp import FastMCP
from pathlib import Path
import asyncio

mcp = FastMCP(name="Shell")
REPO_ROOT = Path(__file__).resolve().parents[3]

@mcp.tool()
async def bash(
    command: A[str, "Bash command to run"],
    description: A[str, "Why I'm running this command"] = "",
    timeout_seconds: A[int, "Timeout in seconds"] = 120,
) -> str:
    """
    Run a bash command from the repository root and return output.
    """
    _ = description
    safe_command = command.strip()
    safe_timeout = max(1, int(timeout_seconds))
    if not safe_command:
        return "Error: command is empty."

    proc = await asyncio.create_subprocess_exec(
        "bash",
        "--noprofile",
        "--norc",
        "-lc",
        safe_command,
        cwd=str(REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=safe_timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"Error: command timed out after {safe_timeout}s."

    out = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")
    if proc.returncode == 0:
        return out if out else "(no output)"
    return f"Exit code {proc.returncode}\nSTDOUT:\n{out}\nSTDERR:\n{err}"
