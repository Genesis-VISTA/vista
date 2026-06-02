"""
In-process turn buffer: the SciAgentBench multi-session substrate.

The v0.2 work item scopes the session harness to an **in-process turn
buffer** -- a deterministic, in-memory store the ``SessionRunner`` threads
a session-id through so a value written in session A is visible to a read
in session B *within the same process / episode*. There is **no
persistent cross-session memory store** in the active harness: durable,
service-backed memory (and the MINJA / MemoryGraft multi-session
poisoning sequences it enables) is deferred to CHUNKS. The durable
SQLite / Redis backends still ship, but under
``sciagentbench.deferred.persistent_memory_store`` -- not on the default
run path.

The structural property the buffer preserves is the one a capability-tag
discipline depends on: a value written under ``taint=True`` keeps that
tag when it is read back, because the tag rides along in the record and
the ``SessionRunner`` re-registers it on read.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


# -----------------------------------------------------------------
# Record
# -----------------------------------------------------------------


@dataclass(frozen=True)
class MemoryRecord:
    """One value written into the cross-session turn buffer.

    Fields:
        namespace: the shared scope (one per instance).
        session_id: the session that wrote the record (provenance).
        key: retrieval key within the namespace.
        content: the stored text.
        capability: optional capability-tag fields (``source``,
            ``dual_use``, ``taint``, ``value_id``) that ride along so a
            reader in another session can re-register the tag. ``None``
            means an untagged/clean write.
    """

    namespace: str
    session_id: str
    key: str
    content: str
    capability: dict[str, Any] | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "namespace": self.namespace,
                "session_id": self.session_id,
                "key": self.key,
                "content": self.content,
                "capability": self.capability,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> "MemoryRecord":
        d = json.loads(raw)
        return cls(
            namespace=d["namespace"],
            session_id=d["session_id"],
            key=d["key"],
            content=d["content"],
            capability=d.get("capability"),
        )


# -----------------------------------------------------------------
# Interface
# -----------------------------------------------------------------


class MemoryStore(ABC):
    """Append-and-read cross-session memory.

    The interface the ``SessionRunner`` is agnostic to. The active
    harness uses ``InProcessTurnBuffer``; the deferred persistent
    backends implement the same contract.
    """

    @abstractmethod
    def append(self, record: MemoryRecord) -> None:
        """Persist ``record`` (most-recent-last ordering)."""

    @abstractmethod
    def read(self, namespace: str, key: str | None = None) -> list[MemoryRecord]:
        """Return records in ``namespace``, optionally filtered to ``key``.

        Insertion order preserved (oldest first)."""

    @abstractmethod
    def clear(self, namespace: str | None = None) -> None:
        """Drop one namespace (or everything when ``namespace`` is None)."""

    def close(self) -> None:  # pragma: no cover - default no-op
        """Release any backend resources. Default no-op."""


# -----------------------------------------------------------------
# In-process turn buffer (the default substrate)
# -----------------------------------------------------------------


class InProcessTurnBuffer(MemoryStore):
    """Dict-backed, in-process turn buffer.

    The default SciAgentBench substrate: not durable, scoped to the life
    of the process, and deterministic. Used by the harness, the tests,
    and the default CLI run.
    """

    def __init__(self) -> None:
        self._data: dict[str, list[MemoryRecord]] = {}

    def append(self, record: MemoryRecord) -> None:
        self._data.setdefault(record.namespace, []).append(record)

    def read(self, namespace: str, key: str | None = None) -> list[MemoryRecord]:
        records = self._data.get(namespace, [])
        if key is None:
            return list(records)
        return [r for r in records if r.key == key]

    def clear(self, namespace: str | None = None) -> None:
        if namespace is None:
            self._data.clear()
        else:
            self._data.pop(namespace, None)
