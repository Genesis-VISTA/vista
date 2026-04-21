import sys
import getpass
import json
import logging
import shlex
import subprocess
import dataclasses

import asyncssh
import tenacity
from cachetools import TTLCache
from fastmcp import Context


class TTYSSHClient(asyncssh.SSHClient):
    """SSHClient that prompts via /dev/tty, bypassing the stdin pipe."""

    def kbdint_auth_requested(self) -> str:
        return ""

    def kbdint_challenge_received(
        self, name: str, instructions: str, lang: str, prompts: list[tuple[str, bool]],
    ) -> list[str] | None:
        try:
            tty = open("/dev/tty", "r")
        except OSError:
            return None
        try:
            if instructions:
                print(instructions, file=sys.stderr)
            responses = []
            for prompt, echo in prompts:
                if echo:
                    print(prompt, file=sys.stderr, end="", flush=True)
                    responses.append(tty.readline().rstrip("\n"))
                else:
                    responses.append(getpass.getpass(prompt, stream=sys.stderr))
            return responses
        finally:
            tty.close()


@dataclasses.dataclass
class SSHLoginInfo:
    user: str
    password: str


@dataclasses.dataclass
class Confirmation:
    confrim: bool = False


@tenacity.retry(
    stop = tenacity.stop_after_attempt(4),
    wait = tenacity.wait_random_exponential(multiplier=0.5, max = 10),
    retry = tenacity.retry_if_exception_type(asyncssh.ChannelOpenError),
    reraise = True,
)
async def remote_bash(ssh_conn: asyncssh.SSHClientConnection, command: str, **kwargs) -> str:
    """
    Run a bash command on the remote HPC system.
    Retries on ChannelOpenError. Frontier has MaxSessions set to 1 and sometimes fails
    if you run a command too soon after the previous, so retry with delay when that happens.
    """
    kwargs = {
        "check": False,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        **kwargs,
    }
    result = await ssh_conn.run(f"bash -c {shlex.quote(command)}", **kwargs)
    return result.stdout


@tenacity.retry(
    stop = tenacity.stop_after_attempt(4),
    wait = tenacity.wait_random_exponential(multiplier=0.5, max = 10),
    retry = tenacity.retry_if_exception_type(asyncssh.ChannelOpenError),
    reraise = True,
)
async def scp_retry(*args) -> None:
    """
    Transfer files via scp with the same retry behavior as remote_bash.
    Frontier's MaxSessions=1 sometimes rejects back-to-back channel opens.
    """
    await asyncssh.scp(*args, recurse=True)


def get_tool_call_string(tool: str, /, **kwargs):
    kwargs = {k: v for k, v in kwargs.items() if v != None}
    if kwargs:
        return (
            f"{tool}(\n" +
            ',\n'.join(f"  {k}={json.dumps(v)}" for k, v in kwargs.items()) +
            "\n)"
        )
    else:
        return f"{tool}()"


# Per-(session, host) cache of live SSH connections. Credentials are elicited once per
# session+host and reused across every HPC tool call in that session. Entries time out
# after 1 hour regardless of recent use.
_ssh_connections: TTLCache[tuple[str, str], asyncssh.SSHClientConnection] = TTLCache(maxsize=256, ttl=3600)


async def get_ssh_conn(
    ctx: Context,
    message: str,
    host: str,
    force_confirmation: bool = False,
) -> asyncssh.SSHClientConnection:
    """
    Return a live SSH connection to ``host`` for the current MCP session.

    On first use per (session, host) this elicits credentials via MCP elicitation
    and connects. Subsequent calls return the cached connection. Pass
    ``force_confirmation=True`` to require the user to confirm the tool call even
    when the connection is cached.
    """
    cache_key = (ctx.session_id, host)

    if cache_key not in _ssh_connections:
        logging.info(f"Requesting user login to {host}")
        max_attempts = 3
        attempt = 1
        conn = None
        while attempt <= max_attempts and not conn:
            retry_note = f" (attempt {attempt}/{max_attempts})" if attempt > 1 else ""
            result = await ctx.elicit(
                message=f"Log in to {host}{retry_note} to run:\n{message}",
                response_type=SSHLoginInfo
            )

            if result.action != "accept":
                raise Exception("Unable to launch job, user cancelled login")

            try:
                conn = await asyncssh.connect(host,
                    username = result.data.user,
                    password = result.data.password,
                    login_timeout = 60,
                    connect_timeout = 60,
                )
            except (asyncssh.DisconnectError, asyncssh.PermissionDenied, OSError) as e:
                if attempt >= max_attempts:
                    raise Exception(f"SSH login failed after {max_attempts} attempts: {e}")
                else:
                    logging.warning(f"SSH login attempt {attempt}/{max_attempts} failed: {e}")

            attempt += 1
        _ssh_connections[cache_key] = conn
    elif force_confirmation:
        logging.info(f"Using cached ssh connection to {host}, with forced confirmation")
        result = await ctx.elicit(
            message=f"Confirm running on {host}:\n{message}",
            response_type=Confirmation,
        )
        if result.action != "accept" or not result.data.confrim:
            raise Exception("Job submission cancelled by user")
    else:
        logging.info(f"Using cached ssh connection to {host}")

    return _ssh_connections[cache_key]
