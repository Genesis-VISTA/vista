#!/usr/bin/env python3
"""
Worked example: drive the `alloy-thermo-mc` simulation through the vista API.

Asks the `alloy-design` project to estimate the order-disorder transition temperature
(Tc) of a MoNbTaW composition. The agent turns the prompt into a `submit_hpc_job` call,
the job runs a 112-replica parallel-tempering Monte Carlo on HPC, and a later turn
collects `results.json` and reports Tc.

    # equimolar, the default
    python scripts/example_alloy_tc.py

    # a specific composition, submit and exit without waiting
    python scripts/example_alloy_tc.py --composition 0.30,0.25,0.25,0.20 --submit-only

    # resume: collect a job submitted by an earlier run
    python scripts/example_alloy_tc.py --chat-session <id> --collect-only

Why two turns? The MC job takes minutes and sits in the Slurm queue. Turn 1 submits and
returns a job id; turn 2 (after the job finishes) fetches the output and reports Tc.
Between them this script polls `get_hpc_job_status` through the MCP call endpoint, which
is cheaper than asking the model to poll for you.

Prerequisites
-------------
- A running backend (``./launch.sh``; default http://localhost:8001).
- A model configured (``VISTA_BACKEND_MODEL``) — the agent needs one to plan the call.
- HPC credentials for the target cluster on your user record. Without them the agent
  would stop mid-turn and *elicit* them interactively, which a non-streaming API call
  cannot answer. Set them once (``frontier_s3m_token`` for Frontier; an S3M token is
  scoped to one OLCF project, so each OLCF cluster has its own):

      curl -X PUT http://localhost:8001/users/me \
           -H 'Content-Type: application/json' \
           -d '{"odo_s3m_token": "<your Odo S3M token>"}'

  This script preflights that and tells you if it is missing.

Auth is the dev-only ``X-Vista-User-Email`` header; in prod the API returns 501 until
SSO lands.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from typing import Any

import httpx


DEFAULT_BASE = "http://localhost:8001"
JOB = "alloy-thermo-mc"
# Terminal Slurm states as they appear in get_hpc_job_status's "STATE=" line.
DONE_STATES = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY"}
# The cluster whose credential field gates submission.
CLUSTER_TOKEN_FIELD = {
    "odo": "odo_s3m_token",
    "frontier": "frontier_s3m_token",
    "perlmutter": "nersc_iri_token",
}


def parse_composition(text: str) -> dict[str, float]:
    """`equimolar` or `Mo,Nb,Ta,W` fractions. Must sum to 1.0 — the simplex is physics."""
    if text.strip().lower() in ("equimolar", "equiatomic"):
        return {"mo": 0.25, "nb": 0.25, "ta": 0.25, "w": 0.25}
    parts = [p for p in re.split(r"[,\s]+", text.strip()) if p]
    if len(parts) != 4:
        raise SystemExit(f"--composition needs 4 fractions (Mo,Nb,Ta,W), got {len(parts)}: {text!r}")
    comp = dict(zip(("mo", "nb", "ta", "w"), (float(p) for p in parts)))
    total = sum(comp.values())
    if abs(total - 1.0) > 1e-3:
        raise SystemExit(
            f"composition must sum to 1.0 +/- 1e-3 (got {total:.4f}). These are atom "
            "fractions; rescale before submitting."
        )
    return comp


class Vista:
    """Thin client over the handful of endpoints this example needs."""

    def __init__(self, base_url: str, email: str, timeout: float = 900.0):
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self._headers = {"X-Vista-User-Email": email}

    def me(self) -> dict[str, Any]:
        r = self._http.get("/users/me", params={"config": "true"}, headers=self._headers)
        r.raise_for_status()
        return r.json()

    def new_chat_session(self, project: str, title: str) -> str:
        r = self._http.post(
            f"/projects/{project}/chat-sessions", headers=self._headers, json={"title": title}
        )
        r.raise_for_status()
        return r.json()["id"]

    def ask(self, project: str, prompt: str, chat_session_id: str) -> dict[str, Any]:
        """One agent turn. Returns the ProjectAgentResult."""
        r = self._http.post(
            f"/projects/{project}/agent/run",
            headers=self._headers,
            json={"user_prompt": prompt, "stream": False, "chat_session_id": chat_session_id},
        )
        r.raise_for_status()
        return r.json()

    def call_tool(self, project: str, name: str, arguments: dict) -> str:
        """
        Call an MCP tool directly — no model in the loop, so polling costs no tokens.

        The endpoint returns MCP's raw CallToolResult envelope, not a plain string:
            {"content": [{"type": "text", "text": "..."}], "isError": bool, ...}
        so unwrap the text parts and raise on isError rather than pattern-matching
        against the JSON.
        """
        r = self._http.post(
            f"/projects/{project}/mcp/call",
            headers=self._headers,
            json={"name": name, "arguments": arguments},
        )
        r.raise_for_status()
        out = r.json()
        if isinstance(out, str):
            return out
        text = "\n".join(
            c.get("text", "") for c in (out.get("content") or []) if c.get("type") == "text"
        )
        if out.get("isError"):
            raise RuntimeError(f"{name} failed: {text or out}")
        return text


def agent_text(result: dict[str, Any]) -> str:
    """The assistant's prose from a turn: the text parts of its final message."""
    chunks: list[str] = []
    for msg in result.get("new_messages", []):
        for part in msg.get("parts", []):
            if part.get("part_kind") == "text" and part.get("content"):
                chunks.append(part["content"])
    return "\n".join(chunks).strip()


def tool_calls(result: dict[str, Any]) -> list[tuple[str, Any]]:
    """(tool_name, args) for every tool the agent called this turn."""
    calls = []
    for msg in result.get("new_messages", []):
        for part in msg.get("parts", []):
            if part.get("part_kind") == "tool-call":
                calls.append((part.get("tool_name"), part.get("args")))
    return calls


def find_job_id(result: dict[str, Any]) -> str | None:
    """Pull the Slurm job id out of submit_hpc_job's ground-truth summary."""
    for msg in result.get("new_messages", []):
        for part in msg.get("parts", []):
            if part.get("part_kind") != "tool-return":
                continue
            content = part.get("content")
            text = content if isinstance(content, str) else json.dumps(content)
            m = re.search(r"^\s*job_id:\s*(\S+)", text, re.M)
            if m:
                return m.group(1)
    return None


def poll_until_done(v: Vista, project: str, job_id: str, cluster: str,
                    poll_seconds: int, max_wait: int) -> str:
    """Poll the MCP tool directly until the job leaves the queue. Returns the last status."""
    deadline = time.monotonic() + max_wait
    status = ""
    while True:
        status = v.call_tool(project, "get_hpc_job_status", {"job_id": job_id, "cluster": cluster})
        m = re.search(r"\bSTATE[=:]\s*(\S+)", status)
        state = m.group(1) if m else "UNKNOWN"
        elapsed = int(max_wait - (deadline - time.monotonic()))
        print(f"    [{elapsed:>4}s] STATE={state}")
        if state in DONE_STATES:
            return status
        if time.monotonic() > deadline:
            print(f"    giving up after {max_wait}s (still {state}); the job may still finish.")
            return status
        time.sleep(poll_seconds)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default=DEFAULT_BASE)
    p.add_argument("--user", default="vista-test-admin@americansciencecloud.org",
                   help="Dev identity (X-Vista-User-Email).")
    p.add_argument("--project", default="alloy-design")
    p.add_argument("--composition", default="equimolar",
                   help="'equimolar' or 'Mo,Nb,Ta,W' fractions summing to 1.0.")
    p.add_argument("--cluster", default="odo", choices=("odo", "frontier"))
    p.add_argument("--chat-session", default=None, help="Reuse an existing chat session id.")
    p.add_argument("--submit-only", action="store_true", help="Submit and exit; don't wait.")
    p.add_argument("--collect-only", action="store_true",
                   help="Skip submission; collect + report on an existing --chat-session.")
    p.add_argument("--poll-seconds", type=int, default=30)
    p.add_argument("--max-wait", type=int, default=1800)
    args = p.parse_args(argv)

    comp = parse_composition(args.composition)
    pretty = ", ".join(f"{k.capitalize()}={v}" for k, v in comp.items())
    v = Vista(args.base_url, args.user)

    # --- preflight: credentials, or the agent will elicit mid-turn and stall ---
    me = v.me()
    field = CLUSTER_TOKEN_FIELD[args.cluster]
    if not me.get(field):
        print(f"!! Your user has no {field}, which {args.cluster} submission needs.")
        print(f"!! Set it first:  curl -X PUT {args.base_url}/users/me "
              f"-H 'Content-Type: application/json' -d '{{\"{field}\": \"<token>\"}}'")
        print("!! Without it the agent stops to elicit credentials, which this "
              "non-streaming client cannot answer.")
        return 2
    print(f"==> user {me['email']} ({field} present)")

    session_id = args.chat_session or v.new_chat_session(
        args.project, f"Tc of MoNbTaW ({args.composition})"
    )
    print(f"==> chat session {session_id}")

    job_id = None
    if not args.collect_only:
        # --- turn 1: ask for the estimate; the agent submits the job ---
        prompt = (
            f"Estimate the Tc (order-disorder transition temperature) for "
            f"{args.composition} MoNbTaW ({pretty}). "
            f"Submit a single {JOB} job on {args.cluster} with the default screening "
            f"settings — do not start a campaign. Report the job id and stop; "
            f"I will ask you to collect the result once it finishes."
        )
        print(f"\n==> turn 1: {prompt}\n")
        result = v.ask(args.project, prompt, session_id)
        for name, a in tool_calls(result):
            print(f"    tool: {name}({json.dumps(a) if not isinstance(a, str) else a})")
        print("\n--- agent ---")
        print(agent_text(result) or "(no prose)")

        job_id = find_job_id(result)
        if not job_id:
            print("\n!! No job id in the tool returns — the agent may not have submitted.")
            print("!! Re-read the transcript above; re-run with --chat-session "
                  f"{session_id} once resolved.")
            return 1
        print(f"\n==> submitted job {job_id} on {args.cluster}")
        if args.submit_only:
            print(f"==> --submit-only; resume later with:\n"
                  f"    python scripts/example_alloy_tc.py --chat-session {session_id} --collect-only")
            return 0

        # --- wait: poll the MCP tool directly (no model, no tokens) ---
        print("\n==> polling until the job leaves the queue")
        poll_until_done(v, args.project, job_id, args.cluster, args.poll_seconds, args.max_wait)

    # --- turn 2: collect and interpret ---
    prompt2 = (
        "The job has finished. Fetch its results.json with get_hpc_job_outputs and report: "
        "the Tc from the specific-heat peak, the susceptibility cross-check, whether the two "
        "agree, whether the temperature ladder bracketed the peak, and the short-range-order "
        "parameter. If the peak was not bracketed, say the Tc is not trustworthy and suggest "
        "a widened temperature range."
    )
    print(f"\n==> turn 2: collecting results\n")
    result2 = v.ask(args.project, prompt2, session_id)
    for name, a in tool_calls(result2):
        print(f"    tool: {name}({json.dumps(a) if not isinstance(a, str) else a})")
    print("\n--- agent ---")
    print(agent_text(result2) or "(no prose)")

    usage = result2.get("usage", {})
    print(f"\n==> done. session {session_id}"
          f"{f', job {job_id}' if job_id else ''}"
          f" | requests={usage.get('requests')} tokens={usage.get('total_tokens')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
