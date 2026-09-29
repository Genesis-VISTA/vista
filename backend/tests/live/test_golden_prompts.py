"""Live / validation-lane golden prompts (Milestone D).

These tests hit a running VISTA backend with a real model. They are marked
``live`` and skip unless ``VISTA_RUN_LIVE=1``. Soft-assert that preferred tools
were invoked — do not hard-fail on exact answer wording.

Requires:
  - Stack running (``./launch.sh``) with ``VISTA_MCP_HPC_DRY_RUN=true`` recommended
  - ``VISTA_BACKEND_MODEL`` configured on the backend (not the pytest ``test`` stub)
  - ``VISTA_LIVE_BASE_URL`` (default ``http://127.0.0.1:8001``)
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx
import pytest

pytestmark = pytest.mark.live

ADMIN_EMAIL = "vista-test-admin@americansciencecloud.org"
DEFAULT_BASE = "http://127.0.0.1:8001"


@dataclass(frozen=True)
class GoldenCase:
    project: str
    prompt: str
    preferred_tools: frozenset[str]
    description: str


GOLDENS = (
    GoldenCase(
        project="molten-salt",
        prompt=(
            "Search the literature for FLiBe thermophysical properties. "
            "Use the rag_search tool; keep the answer brief."
        ),
        preferred_tools=frozenset({"rag_search"}),
        description="molten-salt prefers rag_search for literature questions",
    ),
    GoldenCase(
        project="alloy-design",
        prompt=(
            "Set up a campaign to maximize the MoNbTaW transition temperature. "
            "Do not submit any jobs yet."
        ),
        preferred_tools=frozenset({"start_campaign", "set_campaign_spec"}),
        description="alloy-design prefers the campaign tools (agenthpc_* is retired)",
    ),
)


def _base_url() -> str:
    return os.environ.get("VISTA_LIVE_BASE_URL", DEFAULT_BASE).rstrip("/")


def _model_configured() -> bool:
    model = os.environ.get("VISTA_BACKEND_MODEL", "")
    return bool(model) and model != "test"


def _tool_names_from_result(body: dict) -> set[str]:
    """Extract tool names from a non-streaming ProjectAgentResult payload."""
    names: set[str] = set()
    for msg in body.get("new_messages") or []:
        if not isinstance(msg, dict):
            continue
        for part in msg.get("parts") or []:
            if not isinstance(part, dict):
                continue
            name = part.get("tool_name")
            if name:
                names.add(str(name))
    return names


@pytest.fixture(scope="module")
def live_http() -> httpx.Client:
    if not _model_configured():
        pytest.skip(
            "VISTA_BACKEND_MODEL must be set to a real provider "
            "(not 'test') for golden prompts"
        )
    base = _base_url()
    with httpx.Client(base_url=base, timeout=600.0) as client:
        try:
            # Cheap liveness: list projects as admin (dev header auth).
            resp = client.get(
                "/projects",
                headers={"X-Vista-User-Email": ADMIN_EMAIL},
            )
            if resp.status_code >= 500:
                pytest.skip(f"backend unavailable at {base}: {resp.status_code}")
        except httpx.HTTPError as exc:
            pytest.skip(f"backend unavailable at {base}: {exc}")
        yield client


@pytest.mark.parametrize(
    "case",
    GOLDENS,
    ids=[c.project for c in GOLDENS],
)
def test_golden_prompt_soft_tool_allowlist(
    live_http: httpx.Client, case: GoldenCase
) -> None:
    """Soft-assert preferred tools; wording of the final answer is ignored."""
    headers = {"X-Vista-User-Email": ADMIN_EMAIL}
    resp = live_http.post(
        f"/projects/{case.project}/agent/run",
        headers=headers,
        json={"user_prompt": case.prompt, "stream": False},
    )
    if resp.status_code == 404:
        pytest.skip(f"seed project {case.project!r} not present on this deployment")
    resp.raise_for_status()
    called = _tool_names_from_result(resp.json())
    hit = case.preferred_tools & called
    assert hit, (
        f"{case.description}: expected at least one of "
        f"{sorted(case.preferred_tools)}, got {sorted(called) or '(none)'}"
    )
