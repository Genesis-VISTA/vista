"""
MCP for remote HPC job submission.
"""

from __future__ import annotations
import getpass
import sys
import subprocess
import asyncssh
import shlex
from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan

HOST = "frontier.olcf.ornl.gov"

ssh_conn: asyncssh.SSHClientConnection | None = None

def prompt_credentials() -> tuple[str, str]:
    # use sys.stderr to avoid issues when running MCP on stdio
    print(f"\nSSH login required for {HOST}\n", file=sys.stderr, end="")
    print(f"Username: ", file=sys.stderr, end="")
    username = input()
    password = getpass.getpass(prompt="Password: ", stream=sys.stderr)
    return username, password


async def remote_bash(*args: str) -> str:
    """
    Run a bash command on the remote HPC system
    """
    if ssh_conn is None:
        raise RuntimeError("SSH connection is not available.")

    result = await ssh_conn.run(shlex.join(args), check=False,
        stdout = subprocess.PIPE,
        stderr = subprocess.STDOUT,
    )
    return result.stdout


@lifespan
async def app_lifespan(server):
    global ssh_conn

    username, password = prompt_credentials()

    ssh_conn = await asyncssh.connect(
        HOST,
        username=username, password=password,
        known_hosts=None, # TODO
    )

    print(f"Connected to {HOST}\n", file=sys.stderr)

    try:
        yield
    finally:
        ssh_conn.close()


mcp = FastMCP(name="Submit Job", lifespan=app_lifespan)


@mcp.tool()
async def remote_bash_tool(command: str) -> str:
    return await remote_bash('bash', '-c', command)
