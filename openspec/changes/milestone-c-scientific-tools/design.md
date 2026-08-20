## Context

After Milestone B, the agent harness can assert skill/project prompt wiring.
Scientific tools (RAG, sandbox, crypto) and thin UI helpers still lack hermetic
PR coverage. Campaign framework tests are already strong and out of scope here.

## Goals / Non-Goals

**Goals:**

- Hermetic RAG, sandbox confinement (fake executor), skills/seed, crypto tests
- Vitest + required `ui:test` for pure `ui/lib` helpers
- Keep PR CI free of HF model downloads and live microsandbox requirements

**Non-Goals:**

- Nightly live LLM / real HPC (Milestone D)
- Full Playwright E2E (Milestone D)
- VISTAGuard including G3 RAG policy gates
- Expanding campaign framework tests

## Decisions

1. **Committed tiny Chroma DB or mock embedder for RAG**
   - Rationale: CI must not download Hugging Face models.
   - Prefer minimal production seam in `rag_mcp.py` only if mock embedder is required.

2. **Fake executor for `run_bash` in PR CI; real microsandbox marked `sandbox`**
   - Rationale: daemon reliability; confinement policy still tested hermetically.

3. **Vitest for `ui/lib/*` only**
   - Rationale: thin, fast unit coverage; Playwright deferred to D.

4. **Reuse B harness for skills → prompt when available**
   - Rationale: avoid duplicate agent construction helpers.

## Risks / Trade-offs

- [RAG fixture bitrot] → Keep fixture tiny and rebuild scripts marked non-CI helpers
- [Sandbox allow_failure hides regressions] → Unit fake-executor subset is required; live remains advisory
- [Vitest config churn in Next app] → Limit to `ui/lib` pure modules; do not bootstrap component E2E here

## Migration Plan

Sequence: crypto → skills/seed → RAG → sandbox → Vitest/`ui:test`. One focused MR
preferred. After merge, Milestone D adds live lanes only.

## Open Questions

- Mock embedder seam vs committed Chroma snapshot — choose whichever stays hermetic with least production churn
