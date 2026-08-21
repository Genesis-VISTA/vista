## Context

After Milestone B, the agent harness can assert skill/project prompt wiring.
Scientific tools (RAG, sandbox, crypto) and thin UI helpers still lack hermetic
PR coverage. Campaign framework tests are already strong and out of scope here.

## Goals / Non-Goals

**Goals:**

- Hermetic RAG, sandbox volume-boundary tests, skills/seed, crypto tests
- Vitest + required `ui:test` for pure `ui/lib` helpers
- Keep PR CI free of HF model downloads and live microsandbox requirements

**Non-Goals:**

- Nightly live LLM / real HPC (Milestone D)
- Full Playwright E2E (Milestone D)
- VISTAGuard including G3 RAG policy gates
- Expanding campaign framework tests
- Inventing an application-level path jail for the sandbox

## Decisions

1. **Committed tiny Chroma DB or mock embedder for RAG**
   - Rationale: CI must not download Hugging Face models.
   - Prefer minimal production seam in `rag_mcp.py` only if mock embedder is required.

2. **Sandbox approach (1a): test the volume-mount boundary that exists today**
   - There is no application-level path jail; tools pass paths to the guest and
     the security boundary is the volume mount into the microsandbox/container.
   - Unit-test `VISTA_DEV_MCP_VOLUMES` → spawn-args wiring without a live daemon.
   - Escape attempts against a real container stay `@pytest.mark.sandbox` /
     `allow_failure` until the daemon is reliable.
   - Do not invent a fake-executor path-jail feature in this milestone.

3. **Vitest for `ui/lib/*` only**
   - Rationale: thin, fast unit coverage; Playwright deferred to D.

4. **Reuse B harness for skills → prompt when available**
   - Rationale: avoid duplicate agent construction helpers.

5. **Missing skill slugs warn and skip (pin current behavior)**
   - Production `_setup_volumes` / `to_prompt` warn and skip unknown slugs.
   - Tests pin that behavior rather than inventing a hard error.

6. **Seed data lives inline in `seed.py`**
   - There is no `defaults.py`; snapshot tests target `seed.py` only.

7. **Three focused MRs**
   - MR 1: backend crypto + skills/seed
   - MR 2: RAG + sandbox volume-boundary
   - MR 3: Vitest + `ui:test`

## Risks / Trade-offs

- [RAG fixture bitrot] → Keep fixture tiny and rebuild scripts marked non-CI helpers
- [Sandbox allow_failure hides regressions] → Volume-mount wiring unit tests are required; live remains advisory
- [Vitest config churn in Next app] → Limit to `ui/lib` pure modules; do not bootstrap component E2E here

## Migration Plan

Sequence across three MRs: crypto + skills/seed → RAG + sandbox (1a) →
Vitest/`ui:test`. After merge, Milestone D adds live lanes only.

## Open Questions

- Mock embedder seam vs committed Chroma snapshot — choose whichever stays hermetic with least production churn
