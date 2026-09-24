"""Tests for the per-session SSH connection cache in lib/ssh.

A login costs the user an RSA passcode per hop (two for Lux: hub, then login
node), so the cache must reuse live connections, serialize concurrent first
logins into one prompt, drop dead connections, and never keep a half-built
jump chain.
"""

import asyncio
import types

import pytest

from vista_mcp_server.lib import ssh


class FakeConn:
    def __init__(self, host, tunnel):
        self.host = host
        self.tunnel = tunnel
        self.closed = False

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True


class FakeCtx:
    def __init__(self, session_id="s1", action="accept"):
        self.session_id = session_id
        self.action = action
        self.prompts: list[str] = []

    async def elicit(self, message, response_type):
        self.prompts.append(message)
        await asyncio.sleep(0)  # let concurrent callers interleave
        return types.SimpleNamespace(
            action=self.action,
            data=response_type(username="user", password="1234567890"),
        )


@pytest.fixture
def connects(monkeypatch):
    """Record every asyncssh.connect call; fail on hosts listed in `fail_on`."""
    calls: list[dict] = []
    fail_on: set[str] = set()

    async def fake_connect(host, **kwargs):
        calls.append({"host": host, **kwargs})
        if host in fail_on:
            raise OSError(f"cannot reach {host}")
        return FakeConn(host, kwargs.get("tunnel"))

    monkeypatch.setattr(ssh.asyncssh, "connect", fake_connect)
    monkeypatch.setattr(ssh, "_ssh_connections", ssh.TTLCache(maxsize=64, ttl=3600))
    monkeypatch.setattr(ssh, "_ssh_login_locks", ssh.TTLCache(maxsize=64, ttl=3600))
    calls_ns = types.SimpleNamespace(calls=calls, fail_on=fail_on)
    return calls_ns


LUX = ["hub.ccs.ornl.gov", "login1.lux.olcf.ornl.gov"]


@pytest.mark.unit
@pytest.mark.anyio
async def test_jump_chain_prompts_once_per_hop_then_reuses(connects):
    ctx = FakeCtx()
    conn = await ssh.get_ssh_conn_mcp_elicitation(ctx, "submit", LUX)

    assert [c["host"] for c in connects.calls] == LUX
    assert conn.host == LUX[-1]
    assert conn.tunnel.host == LUX[0]
    assert all(
        c["keepalive_interval"] == ssh.SSH_KEEPALIVE_INTERVAL for c in connects.calls
    )
    assert len(ctx.prompts) == 2
    assert "jump host" in ctx.prompts[0]
    assert "new passcode" in ctx.prompts[1]

    again = await ssh.get_ssh_conn_mcp_elicitation(ctx, "status", LUX)
    assert again is conn
    assert len(ctx.prompts) == 2
    assert len(connects.calls) == 2


@pytest.mark.unit
@pytest.mark.anyio
async def test_single_host_has_no_passcode_hint(connects):
    ctx = FakeCtx()
    await ssh.get_ssh_conn_mcp_elicitation(ctx, "submit", "andes.olcf.ornl.gov")
    assert len(ctx.prompts) == 1
    assert "new passcode" not in ctx.prompts[0]


@pytest.mark.unit
@pytest.mark.anyio
async def test_cache_is_per_session_and_per_chain(connects):
    a = await ssh.get_ssh_conn_mcp_elicitation(FakeCtx("s1"), "m", LUX)
    b = await ssh.get_ssh_conn_mcp_elicitation(FakeCtx("s2"), "m", LUX)
    c = await ssh.get_ssh_conn_mcp_elicitation(
        FakeCtx("s1"), "m", "andes.olcf.ornl.gov"
    )
    assert len({id(a), id(b), id(c)}) == 3


@pytest.mark.unit
@pytest.mark.anyio
async def test_concurrent_first_logins_share_one_prompt(connects):
    ctx = FakeCtx()
    conns = await asyncio.gather(
        *(ssh.get_ssh_conn_mcp_elicitation(ctx, "m", LUX) for _ in range(5))
    )
    assert all(c is conns[0] for c in conns)
    assert len(ctx.prompts) == 2


@pytest.mark.unit
@pytest.mark.anyio
async def test_closed_connection_is_evicted_and_reprompts(connects):
    ctx = FakeCtx()
    first = await ssh.get_ssh_conn_mcp_elicitation(ctx, "m", LUX)
    first.close()
    second = await ssh.get_ssh_conn_mcp_elicitation(ctx, "m", LUX)
    assert second is not first
    assert len(ctx.prompts) == 4


@pytest.mark.unit
def test_cache_hit_restarts_ttl(monkeypatch):
    now = [0.0]
    cache = ssh.TTLCache(maxsize=4, ttl=10, timer=lambda: now[0])
    monkeypatch.setattr(ssh, "_ssh_connections", cache)
    conn = FakeConn("h", None)
    cache[("s1", ("h",))] = conn

    now[0] = 8
    assert ssh._cached_ssh_connection("s1", ("h",)) is conn
    now[0] = 16  # 16s after insert, but only 8s after the hit
    assert ssh._cached_ssh_connection("s1", ("h",)) is conn
    now[0] = 27
    assert ssh._cached_ssh_connection("s1", ("h",)) is None


@pytest.mark.unit
@pytest.mark.anyio
async def test_failed_second_hop_closes_first_and_caches_nothing(connects):
    connects.fail_on.add(LUX[1])
    ctx = FakeCtx()
    with pytest.raises(OSError):
        await ssh.get_ssh_conn_mcp_elicitation(ctx, "m", LUX)
    hub_conns = [c for c in connects.calls if c["host"] == LUX[0]]
    assert len(hub_conns) == 1
    assert connects.calls[1]["tunnel"].closed
    assert len(ssh._ssh_connections) == 0


@pytest.mark.unit
@pytest.mark.anyio
async def test_cancelled_login_raises_and_caches_nothing(connects):
    ctx = FakeCtx(action="cancel")
    with pytest.raises(Exception, match="cancelled login"):
        await ssh.get_ssh_conn_mcp_elicitation(ctx, "m", LUX)
    assert connects.calls == []
    assert len(ssh._ssh_connections) == 0
