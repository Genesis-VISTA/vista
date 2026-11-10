"""Live check of the HPC availability cards against the real facilities.

Marked ``live`` -- skipped unless ``VISTA_RUN_LIVE=1`` -- and skipped too when
no backend answers. It talks to a running backend (``VISTA_LIVE_BASE_URL``,
default the dev port) rather than holding credentials itself: the checks run
with whatever tokens the signed-in researcher has saved in settings, exactly as
the rail's do.

What it asserts is that every answer is *decisive*: each facility's status
feed is read, and each cluster with a credential resolves to Ready or to a
named failure, never to Couldn't verify. That last state is for a facility
answering in a way the checks do not understand, which is what this test
exists to catch when an IRI deployment changes under us.

Lux is held to the same rule: it is an IRI v2 resource like Frontier. Its own
test reads the public feed directly, so a renamed resource id shows up even
with no Lux token saved.
"""

from __future__ import annotations

import os

import httpx
import pytest

from vista_backend.config import HpcClusterSettings

pytestmark = pytest.mark.live

DEFAULT_BASE = "http://127.0.0.1:8001"
STATES = {
    "ready",
    "degraded",
    "unverifiable",
    "not_connected",
    "rejected",
    "globus_not_connected",
    "globus_session_expired",
}


def _get(path: str) -> dict:
    base = os.environ.get("VISTA_LIVE_BASE_URL", DEFAULT_BASE).rstrip("/")
    try:
        response = httpx.get(f"{base}{path}", timeout=60)
    except httpx.ConnectError:
        pytest.skip(f"no backend at {base}")
    response.raise_for_status()
    return response.json()


def test_every_cluster_gets_a_decisive_answer():
    result = _get("/settings/hpc-status?fresh=true")
    assert result["clusters"], "no clusters visible; unhide one in settings"
    for cluster in result["clusters"]:
        name, checks = cluster["cluster"], cluster["checks"]
        assert cluster["state"] in STATES, name
        facility = checks["facility"]
        assert facility["ok"] or facility["reason"] == "degraded", (
            f"{name}: facility feed not read: {facility['message']}"
        )
        if checks["credential"]["reason"] != "not_connected":
            assert cluster["state"] != "unverifiable", (
                f"{name}: no decisive answer: "
                + "; ".join(c["message"] for c in checks.values() if c and not c["ok"])
            )


def test_single_cluster_recheck_answers_for_every_visible_cluster():
    everything = _get("/settings/hpc-status")
    first = everything["clusters"][0]["cluster"]
    rechecked = _get(f"/settings/hpc-status?fresh=true&cluster={first}")
    assert [c["cluster"] for c in rechecked["clusters"]] == [
        c["cluster"] for c in everything["clusters"]
    ]


def test_response_carries_nothing_token_like():
    body = str(_get("/settings/hpc-status"))
    assert "Bearer" not in body
    assert "refresh_token" not in body


def test_each_olcf_cluster_is_listed_under_its_configured_id():
    """The public v2 feed, no credential: the ids jobs are submitted to are listed."""
    s = HpcClusterSettings()
    for cluster in ("odo", "frontier", "lux"):
        base = getattr(s, f"{cluster}_iri_url").rstrip("/")
        version = getattr(s, f"{cluster}_iri_api_version")
        rows = httpx.get(f"{base}/api/{version}/status/resources", timeout=30).json()
        assert getattr(s, f"{cluster}_iri_resource_id") in {
            r.get("id") for r in rows
        }, cluster
