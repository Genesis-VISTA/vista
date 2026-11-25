import uuid
from datetime import timedelta

import pytest

from vista_backend.config import settings
from vista_backend.services.project_agent import AgentPool, SandboxCapReached


def make_pool() -> tuple[AgentPool, list]:
    closed: list = []

    async def factory(key):
        return object()

    async def cleanup(value):
        closed.append(value)

    return AgentPool(timedelta(hours=1), 1000, factory, cleanup), closed


def key(user: str):
    return (user, None, uuid.uuid4())


@pytest.mark.anyio
async def test_fifth_sandbox_closes_own_lru_idle_only(monkeypatch):
    monkeypatch.setattr(settings, "sandboxes_per_user", 4)
    pool, closed = make_pool()
    alice = [key("alice") for _ in range(4)]
    bob = key("bob")
    for k in [*alice, bob]:
        async with pool.get(k):
            pass
    async with pool.get(key("alice")):
        pass
    await pool.flush()
    assert list(pool.keys()).count(bob) == 1
    assert alice[0] not in list(pool.keys())  # least recently used
    assert all(k in list(pool.keys()) for k in alice[1:])
    assert len(closed) == 1


@pytest.mark.anyio
async def test_refused_when_none_idle(monkeypatch):
    monkeypatch.setattr(settings, "sandboxes_per_user", 2)
    pool, _ = make_pool()
    a, b = key("alice"), key("alice")
    async with pool.get(a), pool.get(b):
        with pytest.raises(SandboxCapReached):
            async with pool.get(key("alice")):
                pass
        async with pool.get(key("bob")):  # others are unaffected
            pass


@pytest.mark.anyio
async def test_held_entry_is_never_evicted(monkeypatch):
    monkeypatch.setattr(settings, "sandboxes_per_user", 1)
    pool, _ = make_pool()
    k = key("alice")
    async with pool.get(k) as agent:
        async with pool.hold(agent):
            pass
    async with pool.get(k) as agent:
        async with pool.hold(agent):
            with pytest.raises(SandboxCapReached):
                async with pool.get(key("alice")):
                    pass


@pytest.mark.anyio
async def test_total_cap_closes_anyones_lru_idle(monkeypatch):
    """At the server's cap a newcomer gets the oldest idle sandbox, whoever's it was."""
    monkeypatch.setattr(settings, "sandboxes_per_user", 4)
    monkeypatch.setattr(settings, "sandboxes_total", 2)
    pool, closed = make_pool()
    alice, bob = key("alice"), key("bob")
    for k in (alice, bob):
        async with pool.get(k):
            pass
    async with pool.get(key("carol")):
        pass
    await pool.flush()
    assert alice not in list(pool.keys()) and bob in list(pool.keys())
    assert len(closed) == 1


@pytest.mark.anyio
async def test_total_cap_refuses_when_nobody_is_idle(monkeypatch):
    monkeypatch.setattr(settings, "sandboxes_per_user", 4)
    monkeypatch.setattr(settings, "sandboxes_total", 2)
    pool, _ = make_pool()
    async with pool.get(key("alice")), pool.get(key("bob")):
        with pytest.raises(SandboxCapReached, match="This server has 2"):
            async with pool.get(key("carol")):
                pass


def test_per_user_cap_defaults_by_deployment(monkeypatch):
    """One code path: a desktop's one user may use the whole total."""
    from vista_backend.services.project_agent import sandboxes_per_user

    monkeypatch.setattr(settings, "sandboxes_per_user", None)
    monkeypatch.setattr(settings, "sandboxes_total", 40)
    monkeypatch.setattr(settings, "deployment", "hosted")
    assert sandboxes_per_user() == 4
    monkeypatch.setattr(settings, "deployment", "desktop")
    assert sandboxes_per_user() == 40
    monkeypatch.setattr(settings, "sandboxes_per_user", 6)
    assert sandboxes_per_user() == 6


def test_a_refused_turn_says_why_without_a_status_code():
    from vista_backend.agents.inference import describe_run_failure

    refused = SandboxCapReached("This server has 40 sandboxes open.")
    assert describe_run_failure(refused) == "This server has 40 sandboxes open."
