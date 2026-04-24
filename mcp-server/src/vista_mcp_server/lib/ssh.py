import sys, getpass, subprocess, shlex
import asyncssh
from collections import OrderedDict
from fastmcp import Context
import tenacity
from pydantic import BaseModel, Field, create_model


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
    confirm: bool = True


async def get_ssh_conn(host: list[str], username: str) -> asyncssh.SSHClientConnection:
    """
    Create an SSH connection, chaining through jump hosts, and prompting on the terminal if
    necessary.
    """
    # asyncssh does not propagate client_factory through to jump hosts if you pass them directly,
    # so we have to create each jump host connection object and pass it via tunnel.
    conn = None
    for h in host:
        conn = await asyncssh.connect(
            h,
            username=username,
            login_timeout=60,
            connect_timeout=60,
            tunnel=conn,
            client_factory=TTYSSHClient,
            known_hosts=None,
        )
    return conn
