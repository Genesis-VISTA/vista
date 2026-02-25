"""
MCP Server to run basic shell commands
"""

from __future__ import annotations
from typing import Annotated as A
from fastmcp import FastMCP
import re
import functools
from .config import settings
import asyncio

mcp = FastMCP(name="Shell")

# I can't find a "shell" mcp server I like so just adding a custom shell tool.
# TODO: Run inside sandbox

@mcp.tool()
async def bash(
    command: A[str, "Absolute path to file, or a URI"],
    description: A[str, ""] = "",
) -> str:
    """
    Run a bash command.

    Avoid commands that produce a large amount of output, and consider piping those outputs to
    files.

    Args:
        command: Bash command to run
        description: Why I'm running this command
    """
    proc = await asyncio.create_subprocess_exec(
        "bash", "-c", command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode()
