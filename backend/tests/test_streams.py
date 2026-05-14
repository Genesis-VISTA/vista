"""Tests for vista_backend.utils.streams.StreamMerger."""
import asyncio

import pytest

from vista_backend.utils.streams import StreamMerger


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _arange(start: int, stop: int, delay: float = 0.0):
    for i in range(start, stop):
        if delay:
            await asyncio.sleep(delay)
        yield i


@pytest.mark.anyio
async def test_merges_multiple_streams():
    async with StreamMerger[int](_arange(0, 3), _arange(10, 13)) as m:
        items = [x async for x in m]
    assert sorted(items) == [0, 1, 2, 10, 11, 12]


@pytest.mark.anyio
async def test_send_injects_items():
    async with StreamMerger[int](_arange(0, 2)) as m:
        m.send(99)
        items = [x async for x in m]
    assert 99 in items
    assert sorted(items) == [0, 1, 99]


@pytest.mark.anyio
async def test_auto_close_ends_when_streams_exhausted():
    async with StreamMerger[int](_arange(0, 2)) as m:
        items = [x async for x in m]
    assert items == [0, 1]


@pytest.mark.anyio
async def test_explicit_close_required_when_auto_close_false():
    async with StreamMerger[str](auto_close=False) as m:
        async def consume():
            return [x async for x in m]

        consumer = asyncio.create_task(consume())
        m.send("a")
        m.send("b")
        await asyncio.sleep(0)  # let consumer drain
        await m.aclose()
        assert await consumer == ["a", "b"]


@pytest.mark.anyio
async def test_stream_error_propagates_to_consumer():
    async def boom():
        yield 1
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        async with StreamMerger[int](boom()) as m:
            async for _ in m:
                pass


@pytest.mark.anyio
async def test_error_in_one_stream_cancels_others_on_exit():
    cancelled = asyncio.Event()

    async def boom():
        raise ValueError("boom")
        yield  # mark as async generator

    async def slow():
        try:
            while True:
                await asyncio.sleep(60)
                yield None
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(ValueError):
        async with StreamMerger[int](boom(), slow()) as m:
            async for _ in m:
                pass

    # __aexit__'s aclose() should have cancelled the still-running slow stream.
    await asyncio.wait_for(cancelled.wait(), timeout=1.0)


@pytest.mark.anyio
async def test_add_stream_after_iteration_started():
    async def consume(m, sink):
        async for x in m:
            sink.append(x)

    async with StreamMerger[int | str](_arange(0, 2, delay=0.005)) as m:
        sink: list = []
        consumer = asyncio.create_task(consume(m, sink))
        await asyncio.sleep(0.002)  # ensure consumer is running

        async def late():
            yield "late"

        m.add_stream(late())
        await consumer
    assert "late" in sink
    assert 0 in sink and 1 in sink


@pytest.mark.anyio
async def test_add_stream_after_exhaustion_raises():
    """With auto_close=True, calling add_stream after all prior streams finished must raise."""
    async def quick():
        yield 1

    async with StreamMerger[int](quick()) as m:
        async for _ in m:
            pass  # drain the stream; auto-close transitions state to "closed"

        async def late():
            yield 2

        with pytest.raises(RuntimeError, match="closed"):
            m.add_stream(late())


@pytest.mark.anyio
async def test_reentering_context_raises():
    async with StreamMerger[int](_arange(0, 1)) as m:
        with pytest.raises(RuntimeError, match="re-entered"):
            await m.__aenter__()


@pytest.mark.anyio
async def test_aclose_is_idempotent():
    async with StreamMerger[int](_arange(0, 1)) as m:
        await m.aclose()
        await m.aclose()  # second call is a no-op


@pytest.mark.anyio
async def test_send_after_close_raises():
    m = StreamMerger[int]()
    await m.aclose()
    with pytest.raises(RuntimeError):
        m.send(1)


@pytest.mark.anyio
async def test_add_stream_after_close_raises():
    m = StreamMerger[int]()
    await m.aclose()
    with pytest.raises(RuntimeError):
        m.add_stream(_arange(0, 1))


@pytest.mark.anyio
async def test_send_after_natural_completion_raises():
    async with StreamMerger[int](_arange(0, 1)) as m:
        async for _ in m:
            pass
        with pytest.raises(RuntimeError):
            m.send(99)


@pytest.mark.anyio
async def test_streams_do_not_start_until_iteration():
    started = asyncio.Event()

    async def gen():
        started.set()
        yield 1

    m = StreamMerger[int](gen())
    # Give the loop a chance — if a task were created, started would be set.
    await asyncio.sleep(0.01)
    assert not started.is_set()
    assert not m._tasks

    # Entering the context alone shouldn't start streams.
    async with m:
        await asyncio.sleep(0.01)
        assert not started.is_set()
        assert not m._tasks

        # First iteration is what spawns the drain task.
        async for _ in m:
            assert started.is_set()


@pytest.mark.anyio
async def test_context_manager_cancels_in_flight_tasks():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def forever():
        started.set()
        try:
            while True:
                await asyncio.sleep(60)
                yield None
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def consume(m):
        async for _ in m:
            pass

    async with StreamMerger[None](forever()) as m:
        # Iterate in the background so the drain task actually starts.
        consumer = asyncio.create_task(consume(m))
        await asyncio.wait_for(started.wait(), timeout=1.0)

    # __aexit__ should cancel both the drain task and the consumer's anext.
    await asyncio.wait_for(cancelled.wait(), timeout=1.0)
    assert m._state == "closed"
    await asyncio.gather(consumer, return_exceptions=True)
