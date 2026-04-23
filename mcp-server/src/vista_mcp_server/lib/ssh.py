import logging
import sys, getpass, subprocess, shlex
import asyncssh
from collections import OrderedDict
from cachetools import TTLCache
from fastmcp import Context
import tenacity
from pydantic import BaseModel, Field, create_model

from ..config import settings


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


class MCPElicitationSSHClient(asyncssh.SSHClient):
    """
    SSHClient that prompts via MCP elicitation.
    """

    def __init__(self, ctx: Context, *,
        login_message: str | None = None, password: str | None = None,
    ):
        super().__init__()
        self._ctx = ctx
        self._login_message = login_message or "Login:"
        self._password = password

    def kbdint_auth_requested(self) -> str:
        return ""

    async def kbdint_challenge_received(
        self, name: str, instructions: str, lang: str, prompts: list[tuple[str, bool]],
    ) -> list[str] | None:
        if not prompts:
            return []
        # Use the pre-provided password if given
        elif self._password and len(prompts) == 1 and not prompts[0][1]:
            password = self._password
            self._password = None
            return [password]

        fields = OrderedDict()
        for i, (prompt_text, echo) in enumerate(prompts):
            # This is kinda hacky, but the Frontend elicitation modal will hide inputs on password_*
            # fields. I can't pass `format: password` as MCP doesn't support it, and
            # json_schema_extra fields get stripped off as well.
            fields[f"{'field' if echo else 'password'}_{i}"] = (str, Field(title=prompt_text))
        ChallengeResponse = create_model("ChallengeResponse", **fields)

        message = self._login_message
        if instructions:
            message = f"{message}\n{instructions}"

        result = await self._ctx.elicit(
            message=message,
            response_type=ChallengeResponse,
        )

        if result.action != "accept":
            return None

        return [getattr(result.data, f) for f in fields.keys()]



@tenacity.retry(
    stop = tenacity.stop_after_attempt(4),
    wait = tenacity.wait_random_exponential(multiplier=0.5, max = 10),
    retry = tenacity.retry_if_exception_type(asyncssh.ChannelOpenError),
    reraise = True,
)
async def ssh_bash_retry(ssh_conn: asyncssh.SSHClientConnection, command: str|list[str], **kwargs) -> str:
    """
    Run a bash command on the remote HPC system

    Retries on ChannelOpenError. Frontier seems to have MaxSessions set to 1, and sometimes fails
    if you run a command too soon after the previous, so retry with delay when that happens.
    """
    if not isinstance(command, str):
        command = shlex.join(command)

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
async def scp_retry(*args):
    """
    Transfer files via scp.

    Retries on ChannelOpenError. Frontier seems to have MaxSessions set to 1, and sometimes fails
    if you run a command too soon after the previous, so retry with delay when that happens.
    """
    await asyncssh.scp(*args, recurse=True)


class SSHLoginInfo(BaseModel):
    username: str
    password: str = ""


class Confirmation(BaseModel):
    confirm: bool = False


# Cache of live SSH connections keyed by (session_id, host chain) so multiple hosts
# can coexist in a single session. Entries time out after 1 hour.
_ssh_connections: TTLCache[tuple[str, tuple[str, ...]], asyncssh.SSHClientConnection] = TTLCache(
    maxsize=128, ttl=3600,
)

async def get_ssh_conn(
    ctx: Context,
    message: str,
    host: str | list[str] | None = None,
    force_confirmation: bool = False,
) -> asyncssh.SSHClientConnection:
    """
    Elicit for SSH credentials, or use the cached SSH connection.
    ``host`` may be a single host or a list of jump hosts ending at the target; when omitted
    it defaults to ``settings.hpc_host``. Pass force_confirmation to always show a confirmation
    prompt even if the connection is cached.
    """
    if host is None:
        hosts = list(settings.hpc_host)
    elif isinstance(host, str):
        hosts = [host]
    else:
        hosts = list(host)
    final_host = hosts[-1]
    cache_key = (ctx.session_id, tuple(hosts))

    if cache_key not in _ssh_connections:
        logging.info(f"User login to {final_host}")

        conn = None
        for i, h in enumerate(hosts):
            is_final_host = (i == len(hosts) - 1)
            if is_final_host:
                prompt_message = f"Log in to {h} to run:\n{message}"
            else:
                prompt_message = f"Log in to {h} (jump host to {final_host})"
            result = await ctx.elicit(message=prompt_message, response_type=SSHLoginInfo)

            if result.action != "accept":
                raise Exception("Unable to launch job, user cancelled login")
            username = result.data.username
            password = result.data.password

            conn = await asyncssh.connect(h,
                username = username,
                login_timeout = 60,
                connect_timeout = 60,
                tunnel = conn,
                client_factory = lambda u=username, host=h, pw=password: MCPElicitationSSHClient(ctx,
                    login_message = f"Log in to {u}@{host}",
                    password = pw,
                ),
            )
        _ssh_connections[cache_key] = conn
    elif force_confirmation:
        logging.info(f"Using cached ssh connection to {final_host}, with forced confirmation")
        result = await ctx.elicit(
            message=f"Confirm running on {final_host}:\n{message}",
            response_type=Confirmation,
        )
        if result.action != "accept" or not result.data.confirm:
            raise Exception("Job submission cancelled by user")
    else:
        logging.info(f"Using cached ssh connection to {final_host}")

    return _ssh_connections[cache_key]
