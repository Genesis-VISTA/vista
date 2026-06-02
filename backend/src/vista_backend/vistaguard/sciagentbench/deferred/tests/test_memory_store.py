"""
Memory-store substrate tests.

Exercises the cross-session plant/retrieve property both store backends
must satisfy (the structural basis of the B3.5 MINJA defense): a value
written in one session, carrying a capability tag, is readable -- tag
intact -- from a different session.
"""

from __future__ import annotations

import pytest

from ..persistent_memory_store import (
    InMemoryStore,
    MemoryRecord,
    SQLiteMemoryStore,
    open_memory_store,
)


@pytest.mark.parametrize("store_factory", [InMemoryStore, lambda: SQLiteMemoryStore(":memory:")])
def test_plant_then_retrieve_across_sessions(store_factory):
    store = store_factory()
    store.append(
        MemoryRecord(
            namespace="ns1",
            session_id="session-A",
            key="fact",
            content="the melting point of X is 9999 K",
            capability={"value_id": "v1", "taint": True, "source": "rag:memory"},
        )
    )
    # A different session reads the namespace.
    records = store.read("ns1")
    assert len(records) == 1
    rec = records[0]
    assert rec.session_id == "session-A"
    assert rec.capability is not None
    assert rec.capability["taint"] is True


@pytest.mark.parametrize("store_factory", [InMemoryStore, lambda: SQLiteMemoryStore(":memory:")])
def test_key_filter_and_ordering(store_factory):
    store = store_factory()
    for i in range(3):
        store.append(
            MemoryRecord(namespace="ns", session_id="s", key="k", content=f"c{i}")
        )
    store.append(MemoryRecord(namespace="ns", session_id="s", key="other", content="x"))
    k_records = store.read("ns", "k")
    assert [r.content for r in k_records] == ["c0", "c1", "c2"]
    assert len(store.read("ns")) == 4


@pytest.mark.parametrize("store_factory", [InMemoryStore, lambda: SQLiteMemoryStore(":memory:")])
def test_clear_namespace(store_factory):
    store = store_factory()
    store.append(MemoryRecord(namespace="a", session_id="s", key="k", content="1"))
    store.append(MemoryRecord(namespace="b", session_id="s", key="k", content="2"))
    store.clear("a")
    assert store.read("a") == []
    assert len(store.read("b")) == 1


def test_namespaces_isolated():
    store = InMemoryStore()
    store.append(MemoryRecord(namespace="a", session_id="s", key="k", content="1"))
    assert store.read("b") == []


def test_open_memory_store_defaults_to_inmemory():
    assert isinstance(open_memory_store(":memory:"), InMemoryStore)
    assert isinstance(open_memory_store("/tmp/sciagentbench_test.db"), SQLiteMemoryStore)
