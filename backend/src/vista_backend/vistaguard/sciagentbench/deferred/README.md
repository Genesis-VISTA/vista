# SciAgentBench — deferred scope (CHUNKS)

This directory holds SciAgentBench work that the **v0.2** scope defers out
of the active harness. Nothing here is on the default run path; it is kept
so the work is not lost when the deferred capabilities land.

| Piece | Why deferred | Revived by |
|---|---|---|
| `persistent_memory_store.py` (SQLite / Redis) | The active harness uses the in-process turn buffer (`sciagentbench/turn_buffer.py`); VISTA has no persistent cross-session memory yet. | **CHUNKS** (persistent cross-session memory) |
| `templates/b3_5_minja.py` + `corpus/b3_5_minja/` | MINJA / MemoryGraft multi-session poisoning needs the persistent store above. | **CHUNKS** |
| `templates/b1_6_cui_extraction.py` + `corpus/b1_6_cui_extraction/` | The v0.2 capability model is `(source, taint, dual-use)` — the CUI / sensitivity tier was dropped; B1 CUI extraction is out of scope until controlled data is ingested. | CUI / sensitivity-tier reinstatement |
| `docker/deferred/sciagentbench-memory/` | The Redis memory-store substrate container, removed from the active two-container set. | **CHUNKS** |

To revive a template, move it back under `sciagentbench/templates/`, re-add
it to `templates/__init__.py:TEMPLATE_BUILDERS`, move its corpus back under
`sciagentbench/corpus/`, and (for B1.6) reinstate the `sensitivity` field on
`CapabilitySpec` and the instance JSON-Schema.
