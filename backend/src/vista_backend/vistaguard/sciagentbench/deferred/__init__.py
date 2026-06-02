"""
Deferred SciAgentBench scope -- parked for CHUNKS, off the default path.

The v0.2 work item defers three things from the active harness, all
collected here so the work is preserved without being on the default run
path:

- ``persistent_memory_store`` -- the durable SQLite / Redis cross-session
  memory backends. The active harness uses the in-process turn buffer
  (``sciagentbench.turn_buffer``); persistent cross-session memory lands
  with CHUNKS.
- ``templates/b3_5_minja`` -- MINJA multi-session memory poisoning, which
  needs the persistent store above.
- ``templates/b1_6_cui_extraction`` -- privacy / CUI extraction, which
  needs the sensitivity/CUI tier dropped from the v0.2 capability model.

Their committed corpus YAMLs live under ``deferred/corpus/`` and are
**not** loaded by the default corpus run.
"""
