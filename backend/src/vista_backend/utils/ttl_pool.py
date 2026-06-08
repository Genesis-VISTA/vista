import asyncio
import logging
from typing import Iterable
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
import time

log = logging.getLogger(__name__)


class TTLPool[Key, Value]:
    """
    Generic helper that manages creating and reusing resources with a time-to-live, and performing
    cleanup when evicting entries.

    `factory(key)` is called to create a new value on a miss. `cleanup(value)` is called on item eviction.

    `get()` returns an async context manager. Entries are pinned for the lifetime of the context
    and will not be evicted (by TTL, LRU, or explicit delete) until released.

    Item cleanup is done in the background. Call flush() or clear() to await cleanup (especially
    necessary before application shutdown.)
    """

    class _Entry[V]:
        def __init__(self, task: asyncio.Task[V]):
            self.last_used = time.monotonic()
            self.task = task
            self.ref_count = 0 # Currently running `get` context managers
            self.idle_event = asyncio.Event()
            self.idle_event.set() # Set when ref_count is 0 and we can safely delete the entry

        def acquire(self) -> None:
            self.ref_count += 1
            self.last_used = time.monotonic()
            self.idle_event.clear()

        def release(self) -> None:
            self.ref_count -= 1
            self.last_used = time.monotonic()
            if self.ref_count == 0:
                self.idle_event.set()

    def __init__(self,
        ttl: timedelta, max_size: int,
        factory: Callable[[Key], Awaitable[Value]],
        cleanup: Callable[[Value], Awaitable[None]],
    ):
        self.ttl = ttl
        self.max_size = max_size
        self._factory = factory
        self._cleanup = cleanup
        self._items: OrderedDict[Key, TTLPool._Entry[Value]] = OrderedDict()
        self._pending_cleanups: set[asyncio.Task] = set()

    def expire(self) -> None:
        """ Evict idle entries past TTL, then oldest idle entries over max_size. In-use entries are skipped. """
        cutoff = time.monotonic() - self.ttl.total_seconds()
        to_delete: list[Key] = []
        pool_size = len(self._items)

        for key, entry in self._items.items():
            if entry.ref_count > 0: # Don't evict if currently in use
                continue
            if entry.last_used < cutoff or pool_size > self.max_size:
                to_delete.append(key)
                pool_size -= 1
            else:
                break

        for key in to_delete:
            entry = self._items[key]
            label = "idle" if entry.last_used < cutoff else "LRU"
            log.info(f"Evicting {label} pool entry {key!r}")
            self._delete(key)

    @asynccontextmanager
    async def get(self, key: Key) -> AsyncIterator[Value]:
        """
        Get an entry, creating it if necessary. Returns an async context manager that pins the
        entry for the duration of the `async with` block, preventing eviction while in use.
        """
        self.expire()

        entry = self._items.get(key)
        if entry is None:
            log.info(f"Creating pool entry {key!r}")
            task = asyncio.ensure_future(self._factory(key))
            entry = TTLPool._Entry(task)
            self._items[key] = entry
        entry.acquire()
        self._items.move_to_end(key)

        try:
            try:
                # shield() prevents cancellation of this coroutine from cascading into
                # entry.task — without it, asyncio would cancel the shared factory task via
                # the awaited future, killing the result for every other waiter too.
                value = await asyncio.shield(entry.task)
            except BaseException:
                # Only evict if the factory itself failed/was cancelled. If we got here because
                # the current coroutine was cancelled while the task is still running (or
                # succeeded), leave the entry so other waiters and future get()s can use it.
                if entry.task.done() and (entry.task.cancelled() or entry.task.exception() is not None):
                    current = self._items.get(key)
                    if current is entry:
                        del self._items[key]
                raise

            yield value
        finally:
            entry.release()
            if key in self._items:
                self._items.move_to_end(key)

    def keys(self) -> Iterable[Key]:
        """ Iterator over keys """
        return self._items.keys()

    def delete(self, key: Key) -> None:
        """ Explicitly deletes and cleans up the entry. Noop if entry is missing. """
        self._delete(key)
        self.expire()

    def _delete(self, key: Key) -> None:
        """ Pop entry and schedule background cleanup, without triggering expire. """
        entry = self._items.pop(key, None)
        if entry is None:
            return

        async def _cleanup() -> None:
            try:
                value = await entry.task
            except BaseException:
                return # factory failed; nothing to clean up
            await entry.idle_event.wait() # wait until no in-use holders remain
            try:
                await self._cleanup(value)
            except Exception:
                log.exception(f"Error cleaning up pool entry {key!r}")

        cleanup_task = asyncio.create_task(_cleanup())
        self._pending_cleanups.add(cleanup_task)
        cleanup_task.add_done_callback(self._pending_cleanups.discard)

    async def flush(self) -> None:
        """ Await all pending cleanup operations """
        while self._pending_cleanups:
            pending = self._pending_cleanups
            self._pending_cleanups = set()
            await asyncio.gather(*pending, return_exceptions=True)

    async def clear(self) -> None:
        """ Delete all items and await cleanup """
        for key in list(self._items):
            self._delete(key)
        await self.flush()
