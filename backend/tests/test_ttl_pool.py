"""Tests for vista_backend.utils.ttl_pool.TTLPool."""

import asyncio
from datetime import timedelta

import pytest

from vista_backend.utils.ttl_pool import TTLPool


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _noop_cleanup(_v: object) -> None:
    return None


@pytest.mark.anyio
async def test_factory_runs_once_per_key():
    calls: list[str] = []

    async def factory(k: str) -> str:
        calls.append(k)
        return f"v-{k}"

    pool = TTLPool[str, str](timedelta(seconds=60), 10, factory, _noop_cleanup)

    async with pool.get("a") as v:
        assert v == "v-a"
    async with pool.get("a") as v:
        assert v == "v-a"
    async with pool.get("b") as v:
        assert v == "v-b"

    assert calls == ["a", "b"]


@pytest.mark.anyio
async def test_concurrent_get_shares_factory_task():
    calls = 0
    gate = asyncio.Event()

    async def factory(k: str) -> str:
        nonlocal calls
        calls += 1
        await gate.wait()
        return f"v-{k}"

    pool = TTLPool[str, str](timedelta(seconds=60), 10, factory, _noop_cleanup)

    async def caller() -> str:
        async with pool.get("a") as v:
            return v

    tasks = [asyncio.create_task(caller()) for _ in range(5)]
    await asyncio.sleep(0)  # let them all enter `get` and subscribe to the task
    gate.set()
    results = await asyncio.gather(*tasks)

    assert results == ["v-a"] * 5
    assert calls == 1


@pytest.mark.anyio
async def test_ttl_eviction_runs_cleanup():
    cleaned: list[str] = []

    async def factory(k: str) -> str:
        return f"v-{k}"

    async def cleanup(v: str) -> None:
        cleaned.append(v)

    pool = TTLPool[str, str](timedelta(milliseconds=20), 10, factory, cleanup)

    async with pool.get("a") as _:
        pass
    await asyncio.sleep(0.05)  # past TTL

    # Any get() triggers expire(); use a different key so 'a' is purely evicted.
    async with pool.get("b") as _:
        pass
    await pool.flush()

    assert cleaned == ["v-a"]


@pytest.mark.anyio
async def test_lru_eviction_when_over_max_size():
    cleaned: list[str] = []

    async def factory(k: str) -> str:
        return f"v-{k}"

    async def cleanup(v: str) -> None:
        cleaned.append(v)

    pool = TTLPool[str, str](timedelta(seconds=60), 2, factory, cleanup)

    # Populate three entries while max_size=2. expire() runs at the start of each
    # get(), so 'a' gets evicted when the *next* get after exceeding max fires.
    async with pool.get("a") as _:
        pass
    async with pool.get("b") as _:
        pass
    async with pool.get("c") as _:
        pass
    async with pool.get("d") as _:
        pass

    await pool.flush()
    assert "v-a" in cleaned


@pytest.mark.anyio
async def test_pinned_entry_is_not_evicted_even_past_ttl():
    cleaned: list[str] = []

    async def factory(k: str) -> str:
        return f"v-{k}"

    async def cleanup(v: str) -> None:
        cleaned.append(v)

    pool = TTLPool[str, str](timedelta(milliseconds=20), 10, factory, cleanup)

    async with pool.get("a") as _:
        await asyncio.sleep(0.05)  # well past TTL while we hold the entry
        # Another get() triggers expire(); 'a' must be skipped because ref_count>0.
        async with pool.get("b") as _:
            pass
        assert cleaned == []

    # Now 'a' is released and idle. A subsequent get() will evict it.
    await asyncio.sleep(0.05)
    async with pool.get("c") as _:
        pass
    await pool.flush()
    assert "v-a" in cleaned


@pytest.mark.anyio
async def test_factory_failure_evicts_entry_and_retries():
    calls = 0

    async def factory(k: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("boom")
        return f"v-{k}"

    pool = TTLPool[str, str](timedelta(seconds=60), 10, factory, _noop_cleanup)

    with pytest.raises(ValueError, match="boom"):
        async with pool.get("a") as _:
            pass

    async with pool.get("a") as v:
        assert v == "v-a"

    assert calls == 2


@pytest.mark.anyio
async def test_factory_failure_propagates_to_concurrent_waiters():
    async def factory(_k: str) -> str:
        await asyncio.sleep(0)  # let other waiters subscribe
        raise ValueError("boom")

    pool = TTLPool[str, str](timedelta(seconds=60), 10, factory, _noop_cleanup)

    async def caller() -> None:
        async with pool.get("a") as _:
            pass

    tasks = [asyncio.create_task(caller()) for _ in range(3)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(isinstance(r, ValueError) for r in results)


@pytest.mark.anyio
async def test_waiter_cancellation_keeps_entry_when_task_still_running():
    """Fix verification: a caller cancelled while the factory is still in-flight
    must not evict the entry. The factory result is preserved for other waiters
    and future get()s, so the factory only runs once."""
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def factory(k: str) -> str:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return f"v-{k}"

    pool = TTLPool[str, str](timedelta(seconds=60), 10, factory, _noop_cleanup)

    async def caller() -> str:
        async with pool.get("a") as v:
            return v

    t1 = asyncio.create_task(caller())
    await asyncio.wait_for(started.wait(), timeout=1.0)
    t1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t1

    # Factory is still mid-await; the entry should remain in the pool.
    release.set()
    async with pool.get("a") as v:
        assert v == "v-a"

    assert calls == 1


@pytest.mark.anyio
async def test_delete_runs_cleanup():
    cleaned: list[str] = []

    async def factory(k: str) -> str:
        return f"v-{k}"

    async def cleanup(v: str) -> None:
        cleaned.append(v)

    pool = TTLPool[str, str](timedelta(seconds=60), 10, factory, cleanup)

    async with pool.get("a") as _:
        pass
    pool.delete("a")
    await pool.flush()

    assert cleaned == ["v-a"]


@pytest.mark.anyio
async def test_delete_while_held_defers_cleanup_until_release():
    cleaned: list[str] = []
    cleanup_started = asyncio.Event()

    async def factory(k: str) -> str:
        return f"v-{k}"

    async def cleanup(v: str) -> None:
        cleanup_started.set()
        cleaned.append(v)

    pool = TTLPool[str, str](timedelta(seconds=60), 10, factory, cleanup)

    async with pool.get("a") as _:
        pool.delete("a")
        # Give the background cleanup a chance to (incorrectly) run.
        await asyncio.sleep(0.01)
        assert cleaned == []
        assert not cleanup_started.is_set()

    await pool.flush()
    assert cleaned == ["v-a"]


@pytest.mark.anyio
async def test_delete_missing_key_is_noop():
    pool = TTLPool[str, str](
        timedelta(seconds=60), 10, lambda _k: asyncio.sleep(0, "v"), _noop_cleanup
    )
    pool.delete("missing")  # must not raise
    await pool.flush()


@pytest.mark.anyio
async def test_cleanup_error_is_swallowed():
    async def factory(k: str) -> str:
        return f"v-{k}"

    async def bad_cleanup(_v: str) -> None:
        raise RuntimeError("cleanup boom")

    pool = TTLPool[str, str](timedelta(seconds=60), 10, factory, bad_cleanup)

    async with pool.get("a") as _:
        pass
    pool.delete("a")
    await pool.flush()  # must not raise


@pytest.mark.anyio
async def test_clear_removes_everything():
    cleaned: list[str] = []

    async def factory(k: str) -> str:
        return f"v-{k}"

    async def cleanup(v: str) -> None:
        cleaned.append(v)

    pool = TTLPool[str, str](timedelta(seconds=60), 10, factory, cleanup)

    async with pool.get("a") as _:
        pass
    async with pool.get("b") as _:
        pass

    await pool.clear()

    assert sorted(cleaned) == ["v-a", "v-b"]
    assert pool._items == {}
