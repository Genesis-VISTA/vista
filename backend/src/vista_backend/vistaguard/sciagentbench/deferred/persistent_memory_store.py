"""
Persistent cross-session memory backends -- **deferred to CHUNKS**.

The active SciAgentBench harness uses the in-process turn buffer
(``sciagentbench.turn_buffer.InProcessTurnBuffer``) and ships **no**
persistent cross-session memory store. These durable, service-backed
backends -- and the MINJA / MemoryGraft multi-session poisoning sequences
they enable -- are deferred to the CHUNKS integration, which adds
persistent cross-session memory to VISTA. They are preserved here, off
the default run path, so the work is not lost.

Two backends:

- ``SQLiteMemoryStore`` -- a single-file (or ``:memory:``) SQLite table.
- ``RedisMemoryStore`` -- one Redis list per namespace (lazily imports
  ``redis``); used when a deployment points the harness at the deferred
  Redis substrate container (``docker/deferred/sciagentbench-memory/``).

Both implement the ``MemoryStore`` contract from ``turn_buffer`` so the
``SessionRunner`` is agnostic to which substrate is mounted.
"""

from __future__ import annotations

import json
import sqlite3

from ..turn_buffer import InProcessTurnBuffer, MemoryRecord, MemoryStore

# Back-compat alias: durable-store callers historically imported
# ``InMemoryStore`` from this module.
InMemoryStore = InProcessTurnBuffer


# -----------------------------------------------------------------
# SQLite
# -----------------------------------------------------------------


class SQLiteMemoryStore(MemoryStore):
    """SQLite-backed store. ``path=":memory:"`` for an ephemeral store.

    The table is intentionally tiny: an autoincrement ``id`` (to
    preserve insertion order) plus the record columns. ``capability`` is
    serialized as a JSON string.
    """

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                namespace TEXT NOT NULL,
                session_id TEXT NOT NULL,
                key TEXT NOT NULL,
                content TEXT NOT NULL,
                capability TEXT
            )
            """
        )
        self._conn.commit()

    def append(self, record: MemoryRecord) -> None:
        self._conn.execute(
            "INSERT INTO memory (namespace, session_id, key, content, capability) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                record.namespace,
                record.session_id,
                record.key,
                record.content,
                json.dumps(record.capability) if record.capability is not None else None,
            ),
        )
        self._conn.commit()

    def read(self, namespace: str, key: str | None = None) -> list[MemoryRecord]:
        if key is None:
            rows = self._conn.execute(
                "SELECT namespace, session_id, key, content, capability "
                "FROM memory WHERE namespace = ? ORDER BY id ASC",
                (namespace,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT namespace, session_id, key, content, capability "
                "FROM memory WHERE namespace = ? AND key = ? ORDER BY id ASC",
                (namespace, key),
            ).fetchall()
        return [
            MemoryRecord(
                namespace=r[0],
                session_id=r[1],
                key=r[2],
                content=r[3],
                capability=json.loads(r[4]) if r[4] is not None else None,
            )
            for r in rows
        ]

    def clear(self, namespace: str | None = None) -> None:
        if namespace is None:
            self._conn.execute("DELETE FROM memory")
        else:
            self._conn.execute("DELETE FROM memory WHERE namespace = ?", (namespace,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


# -----------------------------------------------------------------
# Redis (optional substrate)
# -----------------------------------------------------------------


class RedisMemoryStore(MemoryStore):
    """Redis-backed store (one list per namespace).

    Lazily imports ``redis`` so the dependency is only required when a
    deployment actually points the harness at the deferred Redis
    substrate. Records are JSON blobs RPUSH-ed onto
    ``f"{key_prefix}{namespace}"``.
    """

    def __init__(self, url: str, *, key_prefix: str = "sciagentbench:mem:") -> None:
        try:
            import redis  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError(
                "RedisMemoryStore requires the 'redis' package. Install it "
                "or use the in-process turn buffer (the default)."
            ) from exc
        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._prefix = key_prefix

    def _list_key(self, namespace: str) -> str:
        return f"{self._prefix}{namespace}"

    def append(self, record: MemoryRecord) -> None:
        self._client.rpush(self._list_key(record.namespace), record.to_json())

    def read(self, namespace: str, key: str | None = None) -> list[MemoryRecord]:
        raw = self._client.lrange(self._list_key(namespace), 0, -1)
        records = [MemoryRecord.from_json(item) for item in raw]
        if key is None:
            return records
        return [r for r in records if r.key == key]

    def clear(self, namespace: str | None = None) -> None:
        if namespace is None:
            for k in self._client.scan_iter(match=f"{self._prefix}*"):
                self._client.delete(k)
        else:
            self._client.delete(self._list_key(namespace))

    def close(self) -> None:  # pragma: no cover - optional dep
        self._client.close()


# -----------------------------------------------------------------
# Factory
# -----------------------------------------------------------------


def open_memory_store(spec: str = ":memory:") -> MemoryStore:
    """Open a memory store from a URL-ish spec.

    - ``":memory:"`` or ``"memory"`` -> ``InProcessTurnBuffer``
    - ``"redis://..."`` / ``"rediss://..."`` -> ``RedisMemoryStore``
    - ``"sqlite:///path"`` or any filesystem path -> ``SQLiteMemoryStore``

    The default ``":memory:"`` returns an ``InProcessTurnBuffer``. The
    persistent specs are deferred-to-CHUNKS configuration; the active
    harness uses the in-process buffer directly.
    """
    if spec in (":memory:", "memory"):
        return InProcessTurnBuffer()
    if spec.startswith(("redis://", "rediss://")):
        return RedisMemoryStore(spec)
    if spec.startswith("sqlite:///"):
        return SQLiteMemoryStore(spec[len("sqlite:///") :])
    return SQLiteMemoryStore(spec)
