"""
`preview_hpc_job` and `get_hpc_queue_status`: the read-only tools Slack runs
without a confirmation. Preview never contacts the cluster, only S3M to name
the token's project; queue status reads the facility's public IRI status feed,
served here by `httpx.MockTransport`.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx

import pytest
from fastmcp.exceptions import ToolError

import vista_mcp_server.submit_job_mcp as m
from vista_mcp_server.config import settings
from vista_mcp_server.lib import iri as iri_lib
from vista_mcp_server.lib import olcf_token
from vista_mcp_server.lib.user_config import UserConfig

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

CFG = UserConfig(
    frontier_s3m_token="frontier-token",
    lux_s3m_token="lux-token",
)


def _ctx(user: UserConfig = CFG):
    vista = {"user": user.model_dump(), "project_paths": {}}
    return SimpleNamespace(
        request_context=SimpleNamespace(meta=SimpleNamespace(vista=vista))
    )


@pytest.fixture(autouse=True)
def jobs(tmp_path, monkeypatch):
    job = tmp_path / "hpc_jobs" / "flibe-md"
    job.mkdir(parents=True)
    (job / "README.md").write_text("# flibe-md\n\nFLiBe MD.\n", encoding="utf-8")
    (job / "cluster_defaults.json").write_text(
        json.dumps(
            {
                "frontier": {"duration": 3600, "resources": {"node_count": 2}},
                "lux": {"duration": 1800, "resources": {"node_count": 1}},
            }
        ),
        encoding="utf-8",
    )
    (job / "job.frontier.slurm").write_text("echo md\n", encoding="utf-8")
    (job / "job.lux.slurm").write_text("echo md\n", encoding="utf-8")
    monkeypatch.setattr(settings, "local_hpc_jobs_dir", tmp_path / "hpc_jobs")
    monkeypatch.setattr(m, "AVAILABLE_JOBS", m.get_available_jobs())
    olcf_token._introspect_cache.clear()


@pytest.fixture
def s3m(monkeypatch):
    """S3M's introspection: each token's project, or an exception to raise. Records what was asked."""
    projects: dict[str, object] = {"frontier-token": "mat201", "lux-token": "mat202"}
    asked: list[str] = []

    async def introspect(token, *, introspect_url):
        asked.append(token)
        answer = projects[token]
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            return await answer()
        return answer

    monkeypatch.setattr(m, "get_s3m_token_project", introspect)
    return SimpleNamespace(projects=projects, asked=asked)


@pytest.fixture
def no_network(monkeypatch, s3m):
    """The cluster is never contacted; S3M is the stub above."""

    def refuse_client(*args, **kwargs):
        raise AssertionError("preview must not contact the cluster")

    monkeypatch.setattr(m, "client_for", refuse_client)


# --- preview ---------------------------------------------------------------


async def test_preview_applies_the_jobs_defaults(no_network):
    text = await m.preview_hpc_job(_ctx(), job="flibe-md", cluster="frontier")

    assert "cluster: frontier" in text
    assert "nodes: 2" in text
    assert "duration: 1:00:00 (3600s)" in text
    assert "script: flibe-md/job.frontier.slurm" in text
    assert "account: mat201" in text


async def test_preview_shows_what_was_asked_for(no_network):
    text = await m.preview_hpc_job(
        _ctx(),
        job="flibe-md",
        cluster="lux",
        node_count=4,
        duration="0:30:00",
        script_args="--steps 1000 --salt 'Li F'",
    )

    # Lux, like Frontier, charges its S3M token's project.
    assert "account: mat202" in text
    assert "nodes: 4" in text and "duration: 0:30:00" in text
    assert "job.lux.slurm --steps 1000 --salt 'Li F'" in text


async def test_preview_names_a_recently_introspected_project(no_network, s3m):
    olcf_token._introspect_cache[
        (settings.frontier_introspect_url, "frontier-token")
    ] = "mat200"

    text = await m.preview_hpc_job(_ctx(), job="flibe-md", cluster="frontier")

    assert "account: mat200" in text
    assert s3m.asked == [], "a cached project is not asked for again"


async def test_preview_asks_s3m_for_the_project_when_none_is_cached(no_network, s3m):
    """After a restart the cache is empty; the card still names the project."""
    text = await m.preview_hpc_job(_ctx(), job="flibe-md", cluster="frontier")

    assert "account: mat201" in text
    assert s3m.asked == ["frontier-token"]


async def test_preview_falls_back_when_s3m_refuses(no_network, s3m):
    s3m.projects["frontier-token"] = ToolError("S3M token introspection failed (401).")

    text = await m.preview_hpc_job(_ctx(), job="flibe-md", cluster="frontier")

    assert f"account: {m.ACCOUNT_UNKNOWN}" in text
    assert "nodes: 2" in text, "the rest of the preview still answers"


async def test_preview_does_not_wait_long_for_s3m(no_network, s3m, monkeypatch):
    import asyncio

    async def slow():
        await asyncio.sleep(10)
        return "never"

    s3m.projects["frontier-token"] = slow
    monkeypatch.setattr(m, "PREVIEW_INTROSPECT_TIMEOUT", 0.05)

    text = await m.preview_hpc_job(_ctx(), job="flibe-md", cluster="frontier")

    assert f"account: {m.ACCOUNT_UNKNOWN}" in text


@pytest.mark.parametrize(
    ("args", "error"),
    [
        ({"node_count": m.MAX_NODES + 1}, "node_count out of range"),
        ({"duration": "9:00:00"}, "Time limit too large"),
        ({"job": "nope"}, "not recognized"),
        ({"cluster": "odo"}, 'no "odo" section'),
    ],
)
async def test_preview_rejects_what_submit_would(no_network, args, error):
    call = {"job": "flibe-md", "cluster": "frontier", **args}
    with pytest.raises((ToolError, ValueError), match=error):
        await m.preview_hpc_job(_ctx(), **call)


# --- queue status ----------------------------------------------------------


NOW_INCIDENT = {
    "name": "Orion slowdown",
    "type": "planned",
    "status": "degraded",
    "start": "2026-10-16T08:00:00+00:00",
    "end": None,
}


@pytest.fixture
def feed(monkeypatch):
    """
    The public status feed, as each test sets it: `routes[path]` answers that
    path; anything unset is a 404. Records each request's Authorization.
    """
    routes: dict[str, httpx.Response] = {}
    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("Authorization")))
        return routes.get(request.url.path) or httpx.Response(404, json={})

    def make(cluster, token):
        return iri_lib.IriClient(
            iri_lib.iri_target(cluster), token, transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(m, "client_for", make)
    target = iri_lib.iri_target("frontier")

    def path(rel: str) -> str:
        return httpx.URL(target.url(rel)).path

    return SimpleNamespace(
        routes=routes, seen=seen, path=path, resource=target.compute_resource_id
    )


def _resources(feed, status: str) -> None:
    feed.routes[feed.path("status/resources")] = httpx.Response(
        200,
        json=[
            {"id": "someone-else", "current_status": "down"},
            {"id": feed.resource, "current_status": status},
        ],
    )


async def test_queue_status_reports_the_resource_and_its_incidents(feed):
    _resources(feed, "degraded")
    feed.routes[feed.path("status/incidents")] = httpx.Response(
        200,
        json=[
            {
                **NOW_INCIDENT,
                "resource_uris": [f"/api/v2/status/resources/{feed.resource}"],
            },
            {**NOW_INCIDENT, "name": "Elsewhere", "resource_uris": ["/r/someone-else"]},
        ],
    )

    text = await m.get_hpc_queue_status(_ctx(), cluster="frontier")

    assert "status: degraded" in text, "matched by resource id, not position"
    assert "Orion slowdown" in text
    assert "Elsewhere" not in text, "another resource's incident"
    assert "queue depth: not available from IRI" in text
    status_reads = [a for p, a in feed.seen if "/status/" in p and "/compute/" not in p]
    assert status_reads and all(a is None for a in status_reads), (
        "the feed is public: no token"
    )


async def test_no_active_incidents_is_a_404_at_olcf(feed):
    _resources(feed, "up")

    text = await m.get_hpc_queue_status(_ctx(), cluster="frontier")

    assert "status: up" in text and "open incidents: none" in text


async def test_a_resource_the_feed_does_not_list_says_so(feed):
    feed.routes[feed.path("status/resources")] = httpx.Response(200, json=[])

    text = await m.get_hpc_queue_status(_ctx(), cluster="frontier")

    assert "status: not listed" in text


async def test_an_unreachable_feed_is_a_tool_error(feed):
    with pytest.raises(ToolError, match="status feed"):
        await m.get_hpc_queue_status(_ctx(), cluster="frontier")


async def test_no_token_is_a_tool_error(feed):
    with pytest.raises(ToolError):
        await m.get_hpc_queue_status(_ctx(UserConfig()), cluster="frontier")


async def test_tools_are_annotated_read_only():
    tools = {t.name: t for t in await m.mcp.list_tools()}
    for name in ("preview_hpc_job", "get_hpc_queue_status"):
        assert tools[name].annotations.readOnlyHint is True
    assert tools["submit_hpc_job"].annotations.readOnlyHint is False


def test_the_cluster_scripts_cover_every_cluster():
    assert set(m._CLUSTER_SCRIPTS) == set(m.Cluster.__args__)


async def test_queue_status_lists_my_live_jobs_with_my_token(feed):
    _resources(feed, "up")

    def job(id_, state, name):
        return {
            "id": id_,
            "status": {"state": state, "meta_data": {"s3m": {"name": name}}},
        }

    feed.routes[feed.path(f"compute/status/{feed.resource}")] = httpx.Response(
        200,
        json=[job("4471", "queued", "flibe-md"), job("4470", "completed", "old")],
    )

    text = await m.get_hpc_queue_status(_ctx(), cluster="frontier")

    assert "- 4471 flibe-md QUEUED" in text
    assert "4470" not in text, "a finished job is not in the queue"
    listing = [a for p, a in feed.seen if p.endswith(f"compute/status/{feed.resource}")]
    assert listing == ["Bearer frontier-token"]


async def test_a_failed_job_listing_keeps_the_status(feed):
    _resources(feed, "up")

    text = await m.get_hpc_queue_status(_ctx(), cluster="frontier")

    assert "status: up" in text and "your jobs: IRI could not list them" in text
