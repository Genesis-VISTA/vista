"""Optional weekly / manual real-HPC smoke for ``hpc_jobs/example``.

Marked ``hpc`` — skipped unless ``VISTA_RUN_HPC=1``. Prefer weekly schedule or
manual runs; do not put this on the default nightly if the cluster is flaky.

See ``docs/validation-lane.md`` for credentials, expected outcomes, and
cluster limits.
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

pytestmark = pytest.mark.hpc

ADMIN_EMAIL = "vista-test-admin@americansciencecloud.org"
DEFAULT_BASE = "http://127.0.0.1:8001"
CLUSTER = os.environ.get("VISTA_HPC_SMOKE_CLUSTER", "odo")
POLL_TIMEOUT_S = float(os.environ.get("VISTA_HPC_SMOKE_TIMEOUT_S", "600"))


def _base_url() -> str:
    return os.environ.get("VISTA_LIVE_BASE_URL", DEFAULT_BASE).rstrip("/")


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


def _content_text(body: dict) -> str:
    return "".join(
        part.get("text", "")
        for part in body.get("content", [])
        if isinstance(part, dict)
    )


@pytest.fixture
def hpc_http() -> httpx.Client:
    base = _base_url()
    with httpx.Client(base_url=base, timeout=120.0) as client:
        try:
            resp = client.get(
                "/projects",
                headers={"X-Vista-User-Email": ADMIN_EMAIL},
            )
            if resp.status_code >= 500:
                pytest.skip(f"backend unavailable at {base}: {resp.status_code}")
        except httpx.HTTPError as exc:
            pytest.skip(f"backend unavailable at {base}: {exc}")
        yield client


def test_example_job_submits_and_reaches_terminal(hpc_http: httpx.Client) -> None:
    """Submit ``hpc_jobs/example``, record job id, poll to a terminal state.

    Expected (when credentials + cluster allow):
      - submit returns a real job id (not ``dry-…``)
      - status reaches a terminal state (e.g. COMPLETED / FAILED / CANCELLED)
      - outputs MAY be fetchable via ``get_hpc_job_outputs`` (cluster-dependent)

    Cluster limits / missing tokens should skip or fail with a clear message —
    never gate MR merges (this test is excluded from PR CI).
    """
    project = os.environ.get("VISTA_HPC_SMOKE_PROJECT", "molten-salt")
    headers = {"X-Vista-User-Email": ADMIN_EMAIL}

    submit = hpc_http.post(
        f"/projects/{project}/mcp/call",
        headers=headers,
        json={
            "name": "submit_hpc_job",
            "arguments": {"job": "example", "cluster": CLUSTER},
        },
    )
    if submit.status_code == 404:
        pytest.skip(f"project {project!r} not present")
    submit.raise_for_status()
    body = submit.json()
    assert not body.get("isError"), f"submit failed: {_content_text(body)}"
    text = _content_text(body)
    job_id = _job_id_from_text(text)
    assert job_id, f"no job_id in submit response:\n{text}"
    assert not job_id.startswith("dry-"), (
        f"got dry-run id {job_id!r}; unset VISTA_MCP_HPC_DRY_RUN for real HPC smoke"
    )

    deadline = time.monotonic() + POLL_TIMEOUT_S
    state: str | None = None
    status_text = ""
    while time.monotonic() < deadline:
        status = hpc_http.post(
            f"/projects/{project}/mcp/call",
            headers=headers,
            json={
                "name": "get_hpc_job_status",
                "arguments": {"job_id": job_id, "cluster": CLUSTER},
            },
        )
        status.raise_for_status()
        status_body = status.json()
        status_text = _content_text(status_body)
        state = _state_from_text(status_text)
        if state and state.upper() in {
            "COMPLETED",
            "FAILED",
            "CANCELLED",
            "TIMEOUT",
            "NODE_FAIL",
        }:
            break
        time.sleep(5)

    assert state, (
        f"job {job_id} never reported STATE= within {POLL_TIMEOUT_S}s; "
        f"last status:\n{status_text}"
    )

    # Best-effort output fetch — some clusters withhold outputs; record result.
    outputs = hpc_http.post(
        f"/projects/{project}/mcp/call",
        headers=headers,
        json={
            "name": "get_hpc_job_outputs",
            "arguments": {"job_id": job_id, "cluster": CLUSTER},
        },
    )
    outputs.raise_for_status()
    out_body = outputs.json()
    fetchable = not out_body.get("isError", False)
    # Terminal status is required; output fetchability is cluster-dependent
    # and recorded here for weekly triage (see docs/validation-lane.md).
    assert state.upper() in {
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "TIMEOUT",
        "NODE_FAIL",
    }, (
        f"unexpected terminal state {state!r} for job {job_id}; "
        f"outputs_fetchable={fetchable}"
    )
    print(f"hpc smoke ok: job_id={job_id} state={state} outputs_fetchable={fetchable}")
