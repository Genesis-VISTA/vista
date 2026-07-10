#!/usr/bin/env python3
"""
Campaign replay + load generator (evaluation plan M6).

Replays canned alloy/salt "transcripts" (deterministic tool sequences,
`seed=42`) against the running backend HTTP API. Doubles as the benign
corpus for the S3 false-positive measurement. Drives the same client-side
dispatcher (M2) and MCP middleware (M3) the real agent uses, so a run
populates the metrics JSONL for E1 (tool-call latency), E6 (concurrency:
sweep `--concurrency`), and E7b (queue-delay sweep: `--queue-delay`).

Two transcript modes:
  * `tools` (default) — POST /mcp/call with a scripted tool sequence.
    Fully deterministic, no LLM, no HPC credentials (HPC tools run under
    the server's dry-run flag). Exercises M3 + dry-run + queue behavior.
  * `agent`  — POST /agent/run with canned prompts. Needs a model
    configured server-side; adds the M2 client timing and M5 agent_run /
    E12b events. Not deterministic.

Multi-tenancy (E15): `--users N` provisions N distinct dev identities
(admin creates them; each gets its own project) and runs their campaigns
concurrently, selecting identity via the dev-only `X-Vista-User-Email`
header.

HPC realism: start the MCP server with `VISTA_MCP_HPC_DRY_RUN=true`
(and `VISTA_MCP_HPC_QUEUE_DELAY_S=<seconds>` for E7b). loadgen verifies
the dry-run is active (submitted job ids are `dry-…`) and refuses to poll
real clusters by accident.

Usage (from backend/, with the stack running via ./launch.sh):
  uv run python scripts/loadgen.py --concurrency 8 --campaigns 16
  uv run python scripts/loadgen.py --users 4 --concurrency 4        # E15
  uv run python scripts/loadgen.py --queue-delay 300 --poll         # E7b
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from dataclasses import dataclass, field

import httpx

SEED = 42
ADMIN_EMAIL = "vista-test-admin@americansciencecloud.org"

# Canned transcripts: deterministic tool sequences standing in for the two
# hosted campaigns. HPC tools pass `cluster` explicitly because /mcp/call
# does not carry per-user credential metadata (dry-run needs none anyway).
TRANSCRIPTS: dict[str, list[dict]] = {
    "alloy": [
        {"tool": "rag_search", "args": {"query": "refractory high-entropy alloy transition temperature"}},
        {"tool": "submit_hpc_job", "args": {"job": "example", "cluster": "odo"}, "poll": True},
    ],
    "salt": [
        {"tool": "rag_search", "args": {"query": "molten salt thermophysical properties FLiBe"}},
        {"tool": "run_bash", "args": {"command": "echo screening composition"}},
        {"tool": "submit_hpc_job", "args": {"job": "example", "cluster": "odo"}, "poll": True},
    ],
}


@dataclass
class CallResult:
    tool: str
    ok: bool
    ms: float


@dataclass
class CampaignResult:
    app: str
    user: str
    ok: bool
    calls: list[CallResult] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    job_id: str | None = None
    completed: bool = False
    error: str | None = None


def _job_id_from_text(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("job_id:"):
            return line.split(":", 1)[1].strip()
        if line.startswith("JOB_ID="):
            return line.split("=", 1)[1].strip()
    return None


def _state_from_text(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("STATE="):
            return line.split("=", 1)[1].strip()
    return None


class Client:
    """Thin API wrapper that pins an identity via the dev header."""

    def __init__(self, http: httpx.AsyncClient, email: str) -> None:
        self._http = http
        self._headers = {"X-Vista-User-Email": email}
        self.email = email

    async def available_tools(self, project: str) -> set[str]:
        resp = await self._http.get(f"/projects/{project}/mcp/tools", headers=self._headers)
        resp.raise_for_status()
        return {t["name"] for t in resp.json()}

    async def call_tool(self, project: str, tool: str, args: dict) -> tuple[bool, str]:
        resp = await self._http.post(
            f"/projects/{project}/mcp/call",
            headers=self._headers,
            json={"name": tool, "arguments": args},
        )
        resp.raise_for_status()
        body = resp.json()
        text = "".join(
            part.get("text", "") for part in body.get("content", []) if isinstance(part, dict)
        )
        return (not body.get("isError", False)), text

    async def run_agent(self, project: str, prompt: str) -> tuple[bool, str]:
        resp = await self._http.post(
            f"/projects/{project}/agent/run",
            headers=self._headers,
            json={"user_prompt": prompt, "stream": False},
            timeout=600,
        )
        resp.raise_for_status()
        return True, json.dumps(resp.json())[:200]


async def ensure_user(http: httpx.AsyncClient, email: str) -> None:
    """Create a dev user (idempotent), as admin."""
    resp = await http.post(
        "/users",
        headers={"X-Vista-User-Email": ADMIN_EMAIL},
        json={"email": email, "is_admin": False},
    )
    if resp.status_code not in (201, 409, 400):
        resp.raise_for_status()


async def ensure_project(client: Client, name: str) -> None:
    resp = await client._http.post(
        "/projects",
        headers=client._headers,
        json={"name": name, "tools": ["*"], "skills": [], "knowledge_bases": []},
    )
    if resp.status_code not in (201, 409, 400):
        resp.raise_for_status()


async def run_campaign(
    client: Client, project: str, app: str, *, mode: str, poll: bool,
    queue_delay: float, available: set[str] | None = None,
) -> CampaignResult:
    result = CampaignResult(app=app, user=client.email, ok=True)
    try:
        if mode == "agent":
            ok, _ = await client.run_agent(project, f"Run a short {app} screening step.")
            result.ok = ok
            return result
        for step in TRANSCRIPTS[app]:
            # Skip (don't fail on) steps whose tool the project can't reach —
            # e.g. rag_search with no KB configured. Recorded, not silent.
            if available is not None and step["tool"] not in available:
                result.skipped.append(step["tool"])
                continue
            started = time.monotonic()
            ok, text = await client.call_tool(project, step["tool"], step["args"])
            result.calls.append(CallResult(step["tool"], ok, (time.monotonic() - started) * 1000))
            result.ok = result.ok and ok
            if step["tool"] == "submit_hpc_job":
                result.job_id = _job_id_from_text(text)
                if result.job_id and not result.job_id.startswith("dry-"):
                    raise RuntimeError(
                        f"submit returned a real job id {result.job_id!r}; refusing to "
                        "poll a live cluster. Start the MCP server with "
                        "VISTA_MCP_HPC_DRY_RUN=true."
                    )
                if poll and step.get("poll") and result.job_id:
                    result.completed = await _poll_to_completion(
                        client, project, result.job_id, queue_delay
                    )
    except Exception as exc:  # noqa: BLE001 — one campaign failing must not abort the load
        result.ok = False
        result.error = f"{type(exc).__name__}: {exc}"
    return result


async def _poll_to_completion(
    client: Client, project: str, job_id: str, queue_delay: float
) -> bool:
    """Poll status until COMPLETED, bounded by the synthetic queue delay
    plus headroom. Returns True if the job completed in the budget."""
    deadline = time.monotonic() + queue_delay + 30
    interval = max(1.0, min(15.0, (queue_delay or 1) / 5))
    while time.monotonic() < deadline:
        ok, text = await client.call_tool(
            project, "get_hpc_job_status", {"job_id": job_id, "cluster": "odo"}
        )
        if _state_from_text(text) in ("COMPLETED", "CANCELLED", "FAILED"):
            return True
        await asyncio.sleep(interval)
    return False


async def main_async(args: argparse.Namespace) -> None:
    rng = random.Random(SEED)
    apps = list(TRANSCRIPTS)

    # Build the (email, project) tenant set. One identity (the admin) by
    # default; --users provisions N distinct tenants for E15.
    emails = [ADMIN_EMAIL] if args.users <= 1 else [
        f"loadgen-user-{i}@example.com" for i in range(args.users)
    ]

    async with httpx.AsyncClient(base_url=args.base_url, timeout=60) as http:
        for email in emails:
            if email != ADMIN_EMAIL:
                await ensure_user(http, email)

        # Each tenant gets its own project (isolation under concurrency), and
        # we snapshot the tools it can actually reach so the replay adapts to
        # the deployment (e.g. skips rag_search when no KB is configured).
        tenants: list[tuple[Client, str, set[str]]] = []
        for i, email in enumerate(emails):
            client = Client(http, email)
            project = args.project if len(emails) == 1 else f"{args.project}-{i}"
            await ensure_project(client, project)
            tools = await client.available_tools(project) if args.mode == "tools" else set()
            tenants.append((client, project, tools))

        # Assign campaigns round-robin across tenants, deterministically.
        jobs: list[tuple[Client, str, set[str], str]] = []
        for n in range(args.campaigns):
            client, project, tools = tenants[n % len(tenants)]
            jobs.append((client, project, tools, apps[rng.randrange(len(apps))]))

        sem = asyncio.Semaphore(args.concurrency)
        wall_start = time.monotonic()

        async def worker(client: Client, project: str, tools: set[str], app: str) -> CampaignResult:
            async with sem:
                return await run_campaign(
                    client, project, app, mode=args.mode, poll=args.poll,
                    queue_delay=args.queue_delay, available=tools or None,
                )

        results = await asyncio.gather(*(worker(c, p, t, a) for c, p, t, a in jobs))
        wall = time.monotonic() - wall_start

    _report(args, results, wall)


def _report(args: argparse.Namespace, results: list[CampaignResult], wall: float) -> None:
    n = len(results)
    ok = sum(r.ok for r in results)
    completed = sum(r.completed for r in results)
    call_ms = sorted(c.ms for r in results for c in r.calls)
    p95 = call_ms[int(len(call_ms) * 0.95)] if call_ms else 0.0
    print(f"# loadgen ({args.mode} mode), seed={SEED}")
    print(f"- tenants: {min(args.users, n) if args.users > 1 else 1}, "
          f"concurrency: {args.concurrency}")
    print(f"- campaigns: {n} ({ok} ok, {n - ok} failed)")
    print(f"- wall time: {wall:.1f}s  throughput: {n / wall:.2f} campaigns/s")
    if args.poll:
        print(f"- jobs completed (queue_delay={args.queue_delay}s): {completed}/{n}")
    if call_ms:
        print(f"- tool calls: {len(call_ms)}  mean {sum(call_ms)/len(call_ms):.1f}ms  p95 {p95:.1f}ms")
    skipped = {t for r in results for t in r.skipped}
    if skipped:
        print(f"- skipped tools (unavailable in project): {', '.join(sorted(skipped))}")
    for r in results:
        if r.error:
            print(f"  ! {r.user}/{r.app}: {r.error}")
    print("\nMetrics events were written to the server's metrics JSONL; "
          "summarize with: uv run python scripts/metrics_report.py")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8001")
    parser.add_argument("--project", default="loadgen")
    parser.add_argument("--mode", choices=["tools", "agent"], default="tools")
    parser.add_argument("--campaigns", type=int, default=8, help="total campaigns to replay")
    parser.add_argument("--concurrency", type=int, default=4, help="E6: concurrent campaigns")
    parser.add_argument("--users", type=int, default=1, help="E15: distinct tenant identities")
    parser.add_argument("--poll", action="store_true",
                        help="poll HPC jobs to completion (needs server dry-run)")
    parser.add_argument("--queue-delay", type=float, default=0.0,
                        help="E7b: server's synthetic queue delay (s); sizes poll patience")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
