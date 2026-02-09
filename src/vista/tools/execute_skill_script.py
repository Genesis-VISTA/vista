"""
MCP tool implementation for running the salt-analysis skill script safely.
"""
import subprocess
import shlex
import re
import os
from pathlib import Path
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

# Just support .agents and .goose now.
# See https://github.com/vercel-labs/skills/blob/main/src/agents.ts for other agents config dirs
SKILL_DIRECTORIES = [
    ".agents/skills",
    "~/.agents/skills",
    ".goose/skills",
    "~/.config/goose/skills",
]

# We will always execute the scripts directly. If the model adds a "python ./script.py" etc. remove
# it.
IGNORED_COMMAND_PREFIXES = [
    r"python",
    r"python\d",
    r"python\d\.\d+",
    r"bash",
    r"sh",
    r"node",
]


def process_command(command: str):
    args = shlex.split(command, comments=True)

    # Always execute the script directly. Remove any "python ..." prefixes etc.
    if any(re.fullmatch(pat, args[0]) for pat in IGNORED_COMMAND_PREFIXES):
        args = args[1:]

    # Use os.path.abspath to normalize without resolving symlinks
    script = Path(os.path.abspath(args[0]))
    args = args[1:]

    is_skills_script = any(
        script.is_relative_to(Path(skills_dir).expanduser().resolve())
        for skills_dir in SKILL_DIRECTORIES
    )

    if not is_skills_script:
        raise ValueError(f"{script} is not part of an agent skill")

    return [str(script), *args]


def execute_skill_script(command: str):
    args = process_command(command)

    proc = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )

    return ToolResult(
        content=[
            TextContent(type = "text", text = proc.stdout),
        ],
    )
