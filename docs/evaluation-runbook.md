# VISTA Evaluation Runbook

Step-by-step commands to collect platform-overhead, reliability, amortization,
and multi-tenancy metrics for VISTA using the built-in instrumentation. Run the
phases in the order below; each section says what it produces, how to run it,
and whether it needs a live LLM.

> **Two execution modes.** The deterministic load generator has a `tools` mode
> (drives `/mcp/call`, **no LLM, no HPC credentials**) and an `agent` mode
> (drives the full agent loop, **needs a configured `VISTA_BACKEND_MODEL` +
> API key**). Server-side timing (`tool_call.server`, RAG/HPC `stage.*` events)
> is captured in *either* mode. Client-side tool timing (`tool_call.client`),
> VISTAGuard gate timing (`gate.*`), and agent-run summaries (`agent_run`) only
> fire in the **agent loop**, so the measurements that need them are marked
> **[agent-mode]** below.

| Measurement | Mode | Needs |
|---|---|---|
| Infrastructure amortization | — | git only |
| Deployment adoption (DB half) | — | `vista.db` |
| Tool-call latency | tools (server) / agent (full) | stack |
| RAG retrieval latency | tools | stack + a KB |
| HPC submission latency | tools (dry-run) / real | stack |
| Concurrency curve | tools | stack + dry-run |
| Queue-delay behavior | tools | stack + dry-run |
| Fault recovery | **agent** (recovery) / tools (injection) | stack + model |
| Tool-call success rate | from latency traces | — |
| Gate / provenance overhead | **agent** | stack + model |
| Agent quality | **agent** | stack + model |
| Skill reuse | **agent** | production data |
| Multi-tenancy + canary | pytest | — |

Not covered here: largest-campaign / node-hours (read from production job
logs), and the security red-team suite (prompt-injection / knowledge-poisoning
harness), which is maintained separately; this instrumentation ships only the
tenant-isolation/canary test.

---

## 0. Prerequisites and conventions

```bash
cd /path/to/vista          # repo root
./build.sh                 # one-time build of backend + MCP server + UI
mkdir -p results           # where we stash per-run JSONL + reports
```

**Where data lands.** All metric events are JSONL. By default the backend
(client tool timing, gate timing, agent-run) and the MCP server (server-side
tool/stage timing) **both** write to `data/metrics/metrics.jsonl`, so
client- and server-side events for the same run join automatically on
`run_id`. Statistics are computed offline by `backend/scripts/metrics_report.py`
— nothing is aggregated in the hot path.

**The one knob.** `VISTA_BACKEND_METRICS__LEVEL` (backend) and
`VISTA_MCP_METRICS__LEVEL` (MCP server): `off` (default, no-op) → `prod`
(sampled coarse events, safe to leave on permanently) → `perf` (everything,
sampling 1.0 — use this for benchmarking) → `trace`. Granular tri-state
overrides (`__TOOL_TIMING`, `__STAGE_TIMING`, `__GATE_TIMING`,
`__SKILL_USAGE`, …) and `__SAMPLE_RATE` / `__LOG_PATH` refine it. See
`.env.sample` for the full list.

**Rotating between runs.** Runs append to the metrics file. To keep a run
separate, truncate and copy it out:

```bash
: > data/metrics/metrics.jsonl                 # truncate before the run
# … run the measurement …
cp data/metrics/metrics.jsonl results/<name>.jsonl
```

---

## Phase A — Amortization & deployment (pure scripts, no stack)

### Infrastructure amortization

No running services; pure git analysis. **First verify the per-app commit
ranges** in `backend/scripts/amortization.yaml` are still correct (they are
inferred, not tagged), then:

```bash
cd backend
uv run python scripts/amortization_report.py --snapshot   # platform LOC per subsystem
uv run python scripts/amortization_report.py              # per-app summary
uv run python scripts/amortization_report.py --detail     # per-commit breakdown
uv run python scripts/amortization_report.py --latex      # LaTeX-formatted rows
```

Produces the per-app `{skills, job templates, MCP tools, platform LOC
co-changed}` table and the platform-size snapshot. Rerun for any future app by
adding it to `amortization.yaml`.

### Deployment statistics

The **DB half** (users, projects, skills, KBs) works any time against a
`vista.db`. The **usage half** (jobs, campaigns, facilities) is derived from
collected metric events, so it is only populated after the Phase B/D runs or
once a deployment has been collecting with `LEVEL=prod`.

```bash
cd backend
uv run python scripts/metrics_report.py --deployment              # local dev DB
uv run python scripts/metrics_report.py --deployment \
    --db /path/to/deployment/vista.db \
    --metrics /path/to/deployment/metrics.jsonl                   # full adoption report
```

> For real-deployment numbers, point `--db` and `--metrics` at the production
> volume. Start collecting early: run the deployment with
> `VISTA_BACKEND_METRICS__LEVEL=prod` (see Phase D, skill reuse).

---

## Phase B — Systems metrics (live stack, deterministic)

These need the stack running with metrics at `perf` and HPC in dry-run.
**VISTAGuard stays off** for a clean baseline. Launch once:

```bash
# from repo root — env is exported to all three child processes
export VISTA_BACKEND_METRICS__LEVEL=perf
export VISTA_MCP_METRICS__LEVEL=perf
export VISTA_MCP_HPC_DRY_RUN=true            # synthetic submit/status, no real cluster
./launch.sh logs                             # MCP :8000, backend :8001, UI :3000
```

Leave that running; drive load from a second shell (`cd backend`).

### Tool-call latency (mean/p95)

```bash
: > ../data/metrics/metrics.jsonl
uv run python scripts/loadgen.py --campaigns 100 --concurrency 1
cp ../data/metrics/metrics.jsonl results/toolcall.jsonl
uv run python scripts/metrics_report.py --metrics results/toolcall.jsonl
```

Reads `tool_call.server` (and `tool_call.client` if you also run in
agent-mode). `--latex` emits the tool-call row.

### RAG retrieval latency

Requires a Knowledge Base configured on the `loadgen` project (loadgen skips
`rag_search` when none exists — it prints which tools it skipped). Attach a KB
in the UI (or via the API), then:

```bash
: > ../data/metrics/metrics.jsonl
uv run python scripts/loadgen.py --campaigns 100 --concurrency 1
cp ../data/metrics/metrics.jsonl results/rag.jsonl
uv run python scripts/metrics_report.py --metrics results/rag.jsonl
```

The report's `stage.rag.embed/dense/bm25/fusion/citation` rows break RAG
latency down by stage.

### HPC submission latency

Dry-run gives the platform-attributable submission path (`stage.hpc.submit`)
with no facility queue wait — isolating the platform's own overhead:

```bash
: > ../data/metrics/metrics.jsonl
uv run python scripts/loadgen.py --campaigns 50 --concurrency 1 --poll
cp ../data/metrics/metrics.jsonl results/hpc-dryrun.jsonl
uv run python scripts/metrics_report.py --metrics results/hpc-dryrun.jsonl
```

> **Real submits.** For real cluster numbers, relaunch with
> `VISTA_MCP_HPC_DRY_RUN` unset, configure a user's S3M token (UI → User
> settings), and run loadgen in `--mode agent` against an HPC-submitting skill.
> Report `stage.hpc.submit` (platform) separately from queue wait observed via
> `stage.hpc.status` (facility), so the two are not conflated.

### Concurrency curve

Sweep the concurrency level; loadgen prints throughput and p95 per run, and
each file gives a full latency breakdown:

```bash
for N in 1 2 4 8 16 32; do
  : > ../data/metrics/metrics.jsonl
  uv run python scripts/loadgen.py --campaigns $((N*4)) --concurrency "$N"
  cp ../data/metrics/metrics.jsonl "results/conc-n$N.jsonl"
  echo "== N=$N ==" && uv run python scripts/metrics_report.py --metrics "results/conc-n$N.jsonl" | grep tool_call.server
done
```

Plot N vs p95 latency / throughput / failures for the full curve (no arbitrary
cutoff).

### Queue-delay behavior

The synthetic queue delay is an **MCP-server** setting, so relaunch the stack
per delay value (e.g. 5 min / 1 h / 24 h):

```bash
# stop the running stack, then for each delay:
export VISTA_MCP_HPC_DRY_RUN=true
export VISTA_MCP_HPC_QUEUE_DELAY_S=300        # then 3600, then 86400
./launch.sh logs &
# in the load shell:
uv run python scripts/loadgen.py --campaigns 8 --concurrency 4 \
    --queue-delay 300 --poll
```

Records polling overhead per campaign (`stage.hpc.status` count × latency) and
completion. The 86400 (24 h) case is where you pair it with token-expiry (see
fault recovery) to exercise the credential-expiry path honestly.

### Tool-call success rate

No new run — `metrics_report.py` reports `ok%` per event type from the
latency traces (the `tool_call.*` rows).

---

## Phase C — Overhead: gates and provenance **[agent-mode]**

VISTAGuard gates run in the agent's capability hooks, so gate timing only
appears when you drive the **agent loop** (a configured model is required).
Set a model + key first:

```bash
export VISTA_BACKEND_MODEL="anthropic:claude-sonnet-4-6"   # or your provider
export ANTHROPIC_API_KEY=...                               # provider key
export VISTA_BACKEND_METRICS__LEVEL=perf
export VISTA_MCP_METRICS__LEVEL=perf
export VISTA_MCP_HPC_DRY_RUN=true
```

### Per-gate overhead

Replay the same agent workload several times — gates **off**, then G1…G5 each
alone, then all — comparing against the exact no-op baseline:

```bash
export VISTA_BACKEND_VISTAGUARD__PROVENANCE_LOG_PATH=$(pwd)/../data/provenance.jsonl

run_one () {  # $1 = label, remaining = VISTAGuard env assignments
  local label="$1"; shift
  : > ../data/metrics/metrics.jsonl
  env "$@" ./relaunch-and-replay.sh        # see note below
  cp ../data/metrics/metrics.jsonl "results/gate-$label.jsonl"
  uv run python scripts/metrics_report.py --metrics "results/gate-$label.jsonl" | grep -E "gate\.|tool_call"
}

run_one off       VISTA_BACKEND_VISTAGUARD__ENABLED=false
run_one g1   VISTA_BACKEND_VISTAGUARD__ENABLED=true VISTA_BACKEND_VISTAGUARD__G1_ENABLED=true
run_one g2   VISTA_BACKEND_VISTAGUARD__ENABLED=true VISTA_BACKEND_VISTAGUARD__G2_ENABLED=true
run_one g3   VISTA_BACKEND_VISTAGUARD__ENABLED=true VISTA_BACKEND_VISTAGUARD__G3_ENABLED=true
run_one g4   VISTA_BACKEND_VISTAGUARD__ENABLED=true VISTA_BACKEND_VISTAGUARD__G4_ENABLED=true
run_one g5   VISTA_BACKEND_VISTAGUARD__ENABLED=true VISTA_BACKEND_VISTAGUARD__G5_ENABLED=true
run_one all  VISTA_BACKEND_VISTAGUARD__ENABLED=true \
             VISTA_BACKEND_VISTAGUARD__G1_ENABLED=true VISTA_BACKEND_VISTAGUARD__G2_ENABLED=true \
             VISTA_BACKEND_VISTAGUARD__G3_ENABLED=true VISTA_BACKEND_VISTAGUARD__G4_ENABLED=true \
             VISTA_BACKEND_VISTAGUARD__G5_ENABLED=true
```

> `relaunch-and-replay.sh` is shorthand for "(re)start the stack with the given
> env, then run `loadgen.py --mode agent --campaigns N`". Each run's report
> shows `gate.<G>.fast` / `gate.<G>.slow` mean/p95 (per-gate overhead) plus the
> `agent_run` wall time; the % vs. the `off` baseline is the per-gate overhead.
> Note that only the currently wired gates (G1, G3, G5) emit timing today;
> G2/G4 instrumentation is in place pending their activation.

### Provenance overhead

Same agent replay, comparing provenance on vs. off (wall-time delta from the
`agent_run` events):

```bash
# provenance ON
VISTA_BACKEND_VISTAGUARD__ENABLED=true \
VISTA_BACKEND_VISTAGUARD__PROVENANCE_LOG_PATH=$(pwd)/../data/provenance.jsonl \
  ... replay ...  ; cp ../data/metrics/metrics.jsonl results/prov-on.jsonl
# provenance OFF (no log path)
VISTA_BACKEND_VISTAGUARD__ENABLED=true \
  ... replay ...  ; cp ../data/metrics/metrics.jsonl results/prov-off.jsonl
# compare agent_run mean wall time between the two reports
```

Note that provenance writes only on incidents, so the delta is ~0 on benign
traffic; a non-zero number requires adversarial traffic that triggers
incidents.

---

## Phase D — Tenancy & reuse

### Multi-tenancy + canary (pytest, no stack)

The isolation/canary assertions run as a unit test — concurrent two-tenant
campaigns, volume/DB/metadata/metrics isolation, and the canary-credential
negative result:

```bash
cd backend
uv run --extra dev pytest tests/security/test_tenant_isolation.py -v
```

For a live multi-tenant load (isolation at scale), with the stack running:

```bash
uv run python scripts/loadgen.py --users 4 --concurrency 4 --campaigns 16
```

Each tenant gets its own identity (dev-only `X-Vista-User-Email`) and project;
inspect `data/metrics/metrics.jsonl` to confirm each event carries its own
tenant's `session_id`/`project_id`.

### Skill reuse **[agent-mode, production data]**

Skill attribution tags `tool_call.client` events with the owning skill and
records `skills_loaded` per run — both from the agent loop. Best collected from
a real deployment over time:

```bash
# on the deployment — coarse, always-on, full-rate skill data:
export VISTA_BACKEND_METRICS__LEVEL=prod
export VISTA_BACKEND_METRICS__SKILL_USAGE=on
export VISTA_BACKEND_METRICS__SAMPLE_RATE=1.0
# … let it run; then summarize the collected metrics.jsonl with metrics_report.py
```

Report per-skill runs/projects/users. A young deployment will be thin — report
it descriptively rather than overstating.

---

## Phase E — Generate summary tables

Once the JSONL is collected, emit the formatted outputs:

```bash
cd backend
uv run python scripts/metrics_report.py --metrics results/toolcall.jsonl --latex   # latency rows
uv run python scripts/metrics_report.py --deployment --db <prod.db> \
    --metrics <prod-metrics.jsonl>                                                 # adoption table
uv run python scripts/amortization_report.py --latex                               # amortization rows
```

Rows without data print a `\todo{}` placeholder rather than a silent zero — fill
them as each measurement completes.

---

## Fault recovery (note)

Fault injection is an **MCP-server** flag group; relaunch with a profile:

```bash
export VISTA_MCP_HPC_DRY_RUN=true
export VISTA_MCP_FAULT__SUBMIT_FAIL_P=0.2        # or STATUS_TIMEOUT_P / TOKEN_EXPIRE_AFTER_S
./launch.sh logs
```

Injection itself works in either mode, but **auto-recovery** (retry after a
failed submit, re-auth after token expiry) is a decision the **agent** makes,
so measure recovery rate with `loadgen.py --mode agent`. If recovery is still
manual, report that honestly. The startup guard refuses all these flags when
`VISTA_ENV=prod`, so they can never leak into production.
