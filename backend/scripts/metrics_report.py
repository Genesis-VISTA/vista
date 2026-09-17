#!/usr/bin/env python3
"""Metrics report generator (evaluation plan M8).

Joins the metrics JSONL (written by `vista_backend.metrics`, M1-M5) with the
PALISADE provenance JSONL on `run_id`/`session_id`, computes
mean/p50/p95/p99 per event type offline, and renders either a Markdown ops
report or the LaTeX rows for the paper's systems table (tab:eval).

The same command serves the paper experiments and production diagnosis —
events in, statistics out; nothing here touches the runtime.

Usage (from backend/):
  uv run python scripts/metrics_report.py                       # Markdown ops report
  uv run python scripts/metrics_report.py --latex               # Table III rows
  uv run python scripts/metrics_report.py --deployment          # E13 adoption stats (M10)
  uv run python scripts/metrics_report.py \
      --metrics ../data/metrics/metrics.jsonl --provenance logs/provenance.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

# Table III (tab:eval) rows that map directly onto event keys. Stage events
# (M3, server-side) are preferred over client-side tool events when present,
# so facility queue wait never masquerades as platform latency.
LATEX_ROWS: list[tuple[str, str, list[str]]] = [
    ("Tool-call latency (mean/p95)", "instrumented MCP layer", ["tool_call.client"]),
    ("RAG retrieval latency (mean/p95)", "instrumented", ["stage.rag.total", "tool_call.client:rag_search"]),
    ("HPC submission latency (mean/p95)", "S3M/IRI round-trip", ["stage.hpc.submit", "tool_call.client:submit_hpc_job"]),
    ("Agent tool-call success rate", "trace audit", ["tool_call.client"]),
]


@dataclass
class Stats:
    count: int
    ok: int
    mean: float
    p50: float
    p95: float
    p99: float

    @property
    def ok_rate(self) -> float:
        return self.ok / self.count if self.count else 0.0


def percentile(sorted_values: list[float], q: float) -> float:
    """Linear interpolation between closest ranks (numpy default method)."""
    if not sorted_values:
        raise ValueError("no values")
    idx = (len(sorted_values) - 1) * q / 100.0
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_values) - 1)
    frac = idx - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def load_events(paths: list[Path]) -> list[dict]:
    """Load JSONL events; normalizes the provenance emitter's `timestamp`
    key to the metrics schema's `ts` so both logs join uniformly."""
    events: list[dict] = []
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if "ts" not in event and "timestamp" in event:
                event["ts"] = event.pop("timestamp")
            events.append(event)
    return events


def event_keys(event: dict) -> list[str]:
    """Aggregation keys for one event: its type, plus a per-tool key for
    tool-call events (`tool_call.client:rag_search`)."""
    etype = event.get("event_type", "unknown")
    keys = [etype]
    if etype.startswith("tool_call.") and event.get("tool_name"):
        keys.append(f"{etype}:{event['tool_name']}")
    return keys


def summarize_durations(events: list[dict]) -> dict[str, Stats]:
    """mean/p50/p95/p99 and success rate per event key, over events that
    carry a `duration_ms`."""
    durations: dict[str, list[float]] = defaultdict(list)
    ok: dict[str, int] = defaultdict(int)
    for event in events:
        if event.get("duration_ms") is None:
            continue
        for key in event_keys(event):
            durations[key].append(float(event["duration_ms"]))
            ok[key] += event.get("status") == "ok"
    out: dict[str, Stats] = {}
    for key, values in durations.items():
        values.sort()
        out[key] = Stats(
            count=len(values),
            ok=ok[key],
            mean=sum(values) / len(values),
            p50=percentile(values, 50),
            p95=percentile(values, 95),
            p99=percentile(values, 99),
        )
    return out


def summarize_runs(events: list[dict]) -> dict:
    """E12b aggregates over `agent_run` events: completion rate,
    iterations-to-completion, interventions, token spend."""
    runs = [e for e in events if e.get("event_type") == "agent_run"]
    if not runs:
        return {}
    payloads = [r.get("payload", {}) for r in runs]
    stop_reasons = Counter(p.get("stop_reason", "unknown") for p in payloads)
    iterations = [p["iterations"] for p in payloads if p.get("iterations") is not None]
    interventions = [p.get("human_interventions", 0) for p in payloads]
    tokens = [
        (p.get("tokens") or {}).get("input", 0) + (p.get("tokens") or {}).get("output", 0)
        for p in payloads
    ]
    return {
        "runs": len(runs),
        "stop_reasons": dict(stop_reasons),
        "completion_rate": stop_reasons.get("completed", 0) / len(runs),
        "mean_iterations": sum(iterations) / len(iterations) if iterations else None,
        "mean_interventions": sum(interventions) / len(interventions),
        "total_tokens": sum(tokens),
        "sessions": len({r.get("session_id") for r in runs if r.get("session_id")}),
    }


def summarize_gates(events: list[dict]) -> dict[str, dict]:
    """Per-gate decision counts (and duration stats once M4 lands) from the
    provenance `gate_decision` events."""
    gates: dict[str, dict] = {}
    for event in events:
        if event.get("event_type") != "gate_decision":
            continue
        gate = event.get("gate", "unknown")
        info = gates.setdefault(gate, {"count": 0, "allowed": 0, "durations": []})
        info["count"] += 1
        info["allowed"] += bool((event.get("decision") or {}).get("allow"))
        if event.get("duration_ms") is not None:
            info["durations"].append(float(event["duration_ms"]))
    for info in gates.values():
        values = sorted(info.pop("durations"))
        info["mean_ms"] = sum(values) / len(values) if values else None
        info["p95_ms"] = percentile(values, 95) if values else None
    return gates


def join_coverage(metrics: list[dict], provenance: list[dict]) -> dict:
    """How well the two logs join: shared sessions and runs. A near-zero
    overlap usually means the logs come from different deployments."""
    m_sessions = {e.get("session_id") for e in metrics if e.get("session_id")}
    p_sessions = {e.get("session_id") for e in provenance if e.get("session_id")}
    return {
        "metrics_sessions": len(m_sessions),
        "provenance_sessions": len(p_sessions),
        "shared_sessions": len(m_sessions & p_sessions),
        "runs": len({e.get("run_id") for e in metrics if e.get("run_id")}),
    }


def deployment_db_stats(db_path: Path) -> dict:
    """
    E13 adoption counts from the backend SQLite DB (read-only). Raw SQL on
    purpose — the script must work on a copied `vista.db` snapshot without
    booting the backend; the fixture test pins these table/column names
    against the real SQLModel schema.
    """
    import sqlite3

    if not db_path.exists():
        return {}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        q = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
        stats = {
            "users": q("SELECT COUNT(*) FROM app_user"),
            "projects": q("SELECT COUNT(*) FROM project"),
            "project_memberships": q("SELECT COUNT(*) FROM project_member"),
            "skills": q("SELECT COUNT(*) FROM skill"),
            "skills_published": q("SELECT COUNT(*) FROM skill WHERE is_public"),
            "knowledge_bases": q("SELECT COUNT(*) FROM knowledge_base"),
        }
        # Installs = project<->skill attachments (project.skills JSON lists).
        rows = con.execute("SELECT skills FROM project").fetchall()
        stats["skill_installs"] = sum(len(json.loads(r[0] or "[]")) for r in rows)
        return stats
    finally:
        con.close()


def deployment_event_stats(events: list[dict]) -> dict:
    """
    E13/E9 usage counts derived from the metrics JSONL (no job table exists;
    submissions are stateless, so events are the system of record).
    A *campaign* is a session with at least one successful HPC submission;
    facility attribution uses the `cluster` payload field stamped by the
    server-side submit stage (M3) and falls back to "unknown" before it.
    """
    # One submission emits both a client tool event (prod level) and a server
    # stage event (perf level, M3) — count from a single source so a job is
    # never tallied twice. Client events are the primary source; stage events
    # carry the facility attribution.
    client_submits = [e for e in events
                      if e.get("tool_name") == "submit_hpc_job"
                      and str(e.get("event_type", "")).startswith("tool_call.")]
    stage_submits = [e for e in events if e.get("event_type") == "stage.hpc.submit"]
    submits = client_submits or stage_submits
    ok_submits = [e for e in submits if e.get("status") == "ok"]
    per_session: Counter = Counter(e.get("session_id") for e in ok_submits)
    ok_stages = [e for e in stage_submits if e.get("status") == "ok"]
    if ok_stages:
        facilities = Counter((e.get("payload") or {}).get("cluster", "unknown") for e in ok_stages)
    else:
        facilities = Counter({"unknown": len(ok_submits)} if ok_submits else {})
    node_hours = sum(
        (e.get("payload") or {}).get("node_hours") or 0.0 for e in events
    )
    return {
        "agent_runs": len([e for e in events if e.get("event_type") == "agent_run"]),
        "active_sessions": len({e.get("session_id") for e in events if e.get("session_id")}),
        "active_projects": len({e.get("project_id") for e in events if e.get("project_id")}),
        "jobs_submitted": len(ok_submits),
        "jobs_failed": len(submits) - len(ok_submits),
        "jobs_per_facility": dict(facilities),
        "campaigns": len(per_session),
        "largest_campaign_jobs": max(per_session.values(), default=0),
        "node_hours": node_hours or None,
    }


def render_deployment(db: dict, usage: dict) -> str:
    lines = ["# VISTA deployment statistics (M10 / E13)", ""]
    lines += ["| metric | value |", "|---|---|"]
    for key, value in {**db, **usage}.items():
        if key == "jobs_per_facility":
            value = ", ".join(f"{f}: {n}" for f, n in sorted(value.items())) or "-"
        lines.append(f"| {key} | {value if value is not None else 'n/a'} |")
    if db:
        lines += ["", (
            f"Deployment: {db['users']} users, {db['projects']} projects, "
            f"{db['skills']} skills ({db['skills_published']} published, "
            f"{db['skill_installs']} installs), {db['knowledge_bases']} knowledge bases; "
            f"{usage['jobs_submitted']} jobs across {usage['campaigns']} campaigns."
        )]
    return "\n".join(lines)


def render_markdown(stats: dict[str, Stats], runs: dict, gates: dict, coverage: dict) -> str:
    lines = ["# VISTA metrics report (M8)", ""]
    lines += ["## Latency per event type (ms)", "",
              "| event | n | ok% | mean | p50 | p95 | p99 |", "|---|---|---|---|---|---|---|"]
    for key in sorted(stats):
        s = stats[key]
        lines.append(
            f"| {key} | {s.count} | {s.ok_rate:.1%} | {s.mean:.1f} | {s.p50:.1f} | {s.p95:.1f} | {s.p99:.1f} |"
        )
    if runs:
        lines += ["", "## Agent runs (E12b)", ""]
        lines += [f"- runs: {runs['runs']} across {runs['sessions']} sessions",
                  f"- completion rate: {runs['completion_rate']:.1%} ({runs['stop_reasons']})",
                  f"- mean iterations: {runs['mean_iterations']:.1f}" if runs["mean_iterations"] is not None else "- mean iterations: n/a",
                  f"- mean human interventions: {runs['mean_interventions']:.2f}",
                  f"- total tokens: {runs['total_tokens']}"]
    if gates:
        lines += ["", "## PALISADE gates", "", "| gate | n | allow% | mean ms | p95 ms |", "|---|---|---|---|---|"]
        for gate in sorted(gates):
            g = gates[gate]
            mean = f"{g['mean_ms']:.2f}" if g["mean_ms"] is not None else "-"
            p95 = f"{g['p95_ms']:.2f}" if g["p95_ms"] is not None else "-"
            lines.append(f"| {gate} | {g['count']} | {g['allowed'] / g['count']:.1%} | {mean} | {p95} |")
    lines += ["", "## Join coverage", "",
              f"- sessions: {coverage['metrics_sessions']} metrics / {coverage['provenance_sessions']} provenance"
              f" / {coverage['shared_sessions']} shared; runs: {coverage['runs']}"]
    return "\n".join(lines)


def render_latex(stats: dict[str, Stats]) -> str:
    """Rows for tab:eval. Rows without data keep a \\todo{} placeholder so
    partially-instrumented logs never silently print zeros."""
    lines = ["% Generated by scripts/metrics_report.py (M8)"]
    for label, method, keys in LATEX_ROWS:
        s = next((stats[k] for k in keys if k in stats), None)
        if s is None:
            value = "\\todo{}"
        elif "success rate" in label:
            value = f"{s.ok_rate * 100:.1f}\\,\\%"
        else:
            value = f"{s.mean:.0f} / {s.p95:.0f}\\,ms"
        lines.append(f"{label} & {method} & {value} \\\\")
    return "\n".join(lines)


def main() -> None:
    repo_data = Path(__file__).resolve().parents[2] / "data"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", nargs="*", type=Path,
                        default=[repo_data / "metrics" / "metrics.jsonl"])
    parser.add_argument("--provenance", nargs="*", type=Path, default=[])
    parser.add_argument("--latex", action="store_true", help="emit Table III rows")
    parser.add_argument("--deployment", action="store_true",
                        help="E13 adoption stats (DB counts + JSONL-derived usage)")
    parser.add_argument("--db", type=Path, default=repo_data / "vista.db",
                        help="backend SQLite DB (read-only) for --deployment")
    args = parser.parse_args()

    metrics = load_events(args.metrics)

    if args.deployment:
        print(render_deployment(deployment_db_stats(args.db), deployment_event_stats(metrics)))
        return

    provenance = load_events(args.provenance)
    stats = summarize_durations(metrics)

    if args.latex:
        print(render_latex(stats))
        return
    print(render_markdown(
        stats,
        summarize_runs(metrics),
        summarize_gates(provenance),
        join_coverage(metrics, provenance),
    ))


if __name__ == "__main__":
    main()
