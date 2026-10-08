import asyncio
import logging, sys, getpass, subprocess, shlex
import asyncssh
from collections import OrderedDict
from cachetools import TTLCache
from fastmcp import Context
import tenacity
from pydantic import BaseModel, Field, create_model


# Per-(session_id, hosts) cache of live SSH connections. Lets the user
# elicit credentials ONCE per chat-session/host and then reuse the same
# connection for every subsequent tool call — crucial for the multi-worker
# agenthpc pool, which would otherwise re-prompt for every worker's first
# submit, and for jump-host chains like Lux's (hub -> login node), where each
# hop costs the user a fresh RSA passcode. TTL matches agenthpc's per-session
# caches (1 hour) and slides: every cache hit restarts it, so a chat that keeps
# polling a job keeps its connection.
_ssh_connections: TTLCache[tuple[str, tuple[str, ...]], asyncssh.SSHClientConnection] = TTLCache(maxsize=64, ttl=3600)


# Per-(session_id, hosts) lock so that concurrent first-time callers don't
# all race past the cache miss into the elicitation. The first caller wins
# the lock, prompts the user, and stores the connection; subsequent
# callers wait, re-check the cache, and find a hit.
_ssh_login_locks: TTLCache[tuple[str, tuple[str, ...]], asyncio.Lock] = TTLCache(maxsize=64, ttl=3600)


def _ssh_login_lock(session_id: str, hosts: tuple[str, ...]) -> asyncio.Lock:
    key = (session_id, hosts)
    if key not in _ssh_login_locks:
        _ssh_login_locks[key] = asyncio.Lock()
    return _ssh_login_locks[key]


def _cached_ssh_connection(
    session_id: str, hosts: tuple[str, ...],
) -> asyncssh.SSHClientConnection | None:
    """Return the cached SSH connection for (session, hosts) if it is still
    open; otherwise evict the stale entry and return None."""
    cached = _ssh_connections.get((session_id, hosts))
    if cached is None:
        return None
    if cached.is_closed():
        _ssh_connections.pop((session_id, hosts), None)
        return None
    # Re-inserting restarts the entry's TTL (sliding expiry).
    _ssh_connections[(session_id, hosts)] = cached
    return cached


# Seconds between SSH keepalives on cached connections. Without them an idle
# connection (e.g. between job-status polls) can be silently dropped by a
# firewall, and only the next command finds out. With them, a dead connection
# is noticed and closed, so `_cached_ssh_connection` evicts it and the next
# call re-prompts instead of failing.
SSH_KEEPALIVE_INTERVAL = 60


class TTYSSHClient(asyncssh.SSHClient):
    """SSHClient that prompts via /dev/tty, bypassing the stdin pipe."""

    def kbdint_auth_requested(self) -> str:
        return ""

    def kbdint_challenge_received(
        self, name: str, instructions: str, lang: str, prompts: list[tuple[str, bool]],
    ) -> list[str] | None:
        try:
            tty = open("/dev/tty", "r", encoding="utf-8")
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


async def get_ssh_conn_mcp_elicitation(
    ctx: Context, message: str, host: str | list[str],
) -> asyncssh.SSHClientConnection:
    """
    Get an SSH connection for ``(ctx.session_id, host)``, prompting for
    credentials via MCP elicitation if no live connection is cached.

    ``host`` may be a single host or a list of jump hosts ending at the
    target. ``message`` is included in the final-host login prompt so the
    user sees which tool call is about to run.

    Caching: per-session, per-host-chain. The first call for a given
    ``(session_id, hosts)`` pair elicits credentials and stores the live
    connection in ``_ssh_connections``. Subsequent calls return the same
    connection — critical for the multi-worker agenthpc pool, which would
    otherwise prompt for credentials N times. A concurrent stampede of
    first-time callers is serialized through a per-key login lock so only
    ONE elicitation runs. The TTL slides on every hit (see
    ``_cached_ssh_connection``).

    If any hop fails, the hops already opened are closed before the error
    propagates, so a half-built chain is never cached or leaked.
    """
    hosts_tuple = (host,) if isinstance(host, str) else tuple(host)
    hosts = list(hosts_tuple)
    final_host = hosts[-1]

    # Fast path: cache hit.
    cached = _cached_ssh_connection(ctx.session_id, hosts_tuple)
    if cached is not None:
        return cached

    # Slow path: serialize first-time logins so concurrent workers share
    # the elicitation. The second worker waits, then re-checks the cache.
    async with _ssh_login_lock(ctx.session_id, hosts_tuple):
        cached = _cached_ssh_connection(ctx.session_id, hosts_tuple)
        if cached is not None:
            return cached

        logging.info(f"User login to {final_host}")
        opened: list[asyncssh.SSHClientConnection] = []
        conn = None
        try:
            for i, h in enumerate(hosts):
                is_final_host = (i == len(hosts) - 1)
                if is_final_host:
                    prompt_message = f"Log in to {h} to run:\n{message}"
                    if len(hosts) > 1:
                        # One-time passcodes can't be reused across hops.
                        prompt_message += "\n(Use a new passcode, not the one used for the jump host.)"
                else:
                    prompt_message = f"Log in to {h} (jump host to {final_host})"
                result = await ctx.elicit(message=prompt_message, response_type=SSHLoginInfo)
                if result.action != "accept":
                    raise Exception("Unable to launch job, user cancelled login")
                username = result.data.username
                password = result.data.password

                conn = await asyncssh.connect(
                    h,
                    username=username,
                    login_timeout=60,
                    connect_timeout=60,
                    keepalive_interval=SSH_KEEPALIVE_INTERVAL,
                    tunnel=conn,
                    client_factory=lambda u=username, host=h, pw=password: MCPElicitationSSHClient(
                        ctx, login_message=f"Log in to {u}@{host}", password=pw,
                    ),
                    known_hosts=None,
                )
                opened.append(conn)
        except BaseException:
            for c in reversed(opened):
                c.close()
            raise

        _ssh_connections[(ctx.session_id, hosts_tuple)] = conn
        return conn
