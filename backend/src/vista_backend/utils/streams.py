""" Utilities for managing async streams. """
import asyncio
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator
from typing import Literal, Self


_State = Literal["pending", "running", "closed"]

class _Sentinel:
    pass

class _Error:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

class _Value[T]:
    def __init__(self, value: T) -> None:
        self.value = value

class StreamClosedError(RuntimeError):
    pass

class StreamMerger[T]:
    """
    Helper to merge multiple async streams, either from regular async iterables, or even
    integrating with callback based code.

    Items from each added stream are combined into the single output stream. Additional items can be
    pushed one by one via `send()`, and more streams can be registered after iteration has begun
    via `add_stream()`.

    With `auto_close=True`, iteration ends once every registered stream has been exhausted.
    With `auto_close=False`, iteration continues until `aclose()` is called -- useful when the
    merger exists purely as a target for `send()`.

    Errors raised inside any input stream propagate to the consumer.

    Intended for a single consumer; concurrent iteration is not supported.

    Iterating the merger is enough -- cleanup (cancelling in-flight stream tasks) runs
    automatically when iteration finishes or the consumer closes the generator. Using it as
    an `async with` context manager is also supported, and guarantees cleanup even if
    iteration never starts.

    Example:
    ```python
    async def ticks():
        for i in range(3):
            await asyncio.sleep(0.1)
            yield f"tick-{i}"

    merger = StreamMerger[str](ticks())
    merger.send("hello")  # inject a value out-of-band

    async for item in merger:
        print(item)
    ```
    """

    def __init__(
        self,
        *streams: AsyncIterable[T],
        auto_close: bool = True,
    ) -> None:
        self._pending_streams: list[AsyncIterable[T]] = list(streams)
        self._queue: asyncio.Queue[_Value[T] | _Error | _Sentinel] = asyncio.Queue()
        self._tasks: set[asyncio.Task[None]] = set() # starts the streams in background tasks
        self._auto_close = auto_close
        self._state: _State = "pending"
        self._entered = False

    def _mark_closed(self) -> None:
        """ Idempotent transition to 'closed'; queues a sentinel to wake any waiting consumer. """
        if self._state == "closed":
            return
        self._state = "closed"
        self._queue.put_nowait(_Sentinel())

    def _check_auto_close(self) -> None:
        """ Mark closed if auto_close is set and no drain tasks remain. """
        if self._auto_close and not self._tasks:
            self._mark_closed()

    def _start_stream(self, stream: AsyncIterable[T]) -> None:
        """ Starts a stream as a task putting entries onto the queue """
        async def drain_stream():
            try:
                async for item in stream:
                    await self._queue.put(_Value(item))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._queue.put(_Error(exc))

        task = asyncio.create_task(drain_stream())
        self._tasks.add(task)

        def on_drain_done(task: asyncio.Task[None]):
            self._tasks.discard(task)
            self._check_auto_close()

        task.add_done_callback(on_drain_done)

    def add_stream(self, *streams: AsyncIterable[T]) -> None:
        """
        Register one or more streams to add into the merged output.
        """
        if self._state == "pending":
            self._pending_streams.extend(streams)
        elif self._state == "running":
            for stream in streams:
                self._start_stream(stream)
        else: # self._state == "closed"
            raise StreamClosedError("StreamMerger is closed")

    def send(self, item: T) -> None:
        """ Push an item into the merged stream directly. """
        if self._state == "closed":
            raise StreamClosedError("StreamMerger is closed")
        self._queue.put_nowait(_Value(item))

    async def aclose(self) -> None:
        """ Cancel any in-flight stream tasks and end iteration. """
        if self._state == "closed" and not self._tasks:
            return
        tasks = list(self._tasks)
        self._tasks = set()
        for task in tasks:
            task.cancel()
        self._mark_closed()
        if tasks:
            # gather absorbs each task's CancelledError/exception so they don't
            # mask cleanup; our own cancellation still propagates.
            await asyncio.gather(*tasks, return_exceptions=True)

    async def __aenter__(self) -> Self:
        if self._entered:
            raise RuntimeError(f"StreamMerger cannot be re-entered (state: {self._state})")
        self._entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    def __aiter__(self) -> AsyncIterator[T]:
        if self._state != "pending":
            raise RuntimeError("StreamMerger can't be iterated twice")

        async def drain() -> AsyncGenerator[T, None]:
            # aclose() may have run between __aiter__ and the first iteration; don't
            # resurrect a closed merger.
            if self._state == "pending":
                self._state = "running"
                pending = self._pending_streams
                self._pending_streams = []
                for stream in pending:
                    self._start_stream(stream)
                self._check_auto_close()

            try:
                # State may flip to 'closed' before the queue is fully drained (auto-close, or
                # aclose with items still queued). Only stop once both have caught up.
                while not (self._state == "closed" and self._queue.empty()):
                    item = await self._queue.get()
                    if isinstance(item, _Sentinel):
                        break
                    if isinstance(item, _Error):
                        raise item.exc
                    yield item.value
            finally:
                await self.aclose()

        return drain()
