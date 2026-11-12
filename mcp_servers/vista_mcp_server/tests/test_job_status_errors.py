"""
Failures a job watch must tell apart (openspec change background-tasks, task 5.1).

A watch pauses for lost access and waits out a facility's "too many requests",
so both reach it as their own types: from the IRI client (`IriRateLimited`,
`IriCredentialRejected`), from the researcher's settings (`MissingCredential`),
and as structured answers from `read_hpc_job_state`, the state-only reading a
watch polls with. What the agent reads from `get_hpc_job_status` is unchanged.
"""

from __future__ import annotations

from email.utils import format_datetime
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import vista_mcp_server.submit_job_mcp as m
from vista_mcp_server import dry_run
from vista_mcp_server.lib.iri import (
    IriClient,
    IriCredentialRejected,
    IriError,
    IriRateLimited,
    iri_target,
    parse_retry_after,
)
from vista_mcp_server.lib.user_config import MissingCredential, UserConfig
from fakes import FakeIriClient

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


def _client(
    status: int, headers: dict[str, str] | None = None, body: str = ""
) -> IriClient:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers or {}, text=body)

    return IriClient(
        iri_target("frontier"), "token", transport=httpx.MockTransport(respond)
    )


# ---------------------------------------------------------------------------
# The IRI client
# ---------------------------------------------------------------------------


async def test_a_429_is_rate_limited_with_its_retry_after():
    with pytest.raises(IriRateLimited) as raised:
        await _client(429, {"Retry-After": "120"}).get_job_status("123")

    assert raised.value.retry_after == 120
    assert "too many requests" in str(raised.value)
    assert "retry after 120 s" in str(raised.value)
    assert isinstance(raised.value, IriError), (
        "still an IRI error to every other caller"
    )


async def test_a_429_without_retry_after_says_to_wait():
    with pytest.raises(IriRateLimited) as raised:
        await _client(429).get_job_status("123")

    assert raised.value.retry_after is None
    assert "wait a few minutes" in str(raised.value)


def test_retry_after_reads_seconds_and_http_dates():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    later = format_datetime(now + timedelta(seconds=90), usegmt=True)

    assert parse_retry_after("30") == 30
    assert parse_retry_after(later, now=now.timestamp()) == pytest.approx(90)
    assert (
        parse_retry_after(format_datetime(now, usegmt=True), now=now.timestamp() + 5)
        == 0
    )
    assert parse_retry_after("soon") is None
    assert parse_retry_after(None) is None


@pytest.mark.parametrize("status", [401, 403])
async def test_a_refused_token_names_its_cluster(status):
    with pytest.raises(IriCredentialRejected) as raised:
        await _client(status, body="expired").get_job_status("123")

    assert raised.value.cluster == "frontier"
    assert "rejected your Frontier S3M token" in str(raised.value)


@pytest.mark.parametrize(
    ("ask", "cluster"),
    [
        (lambda cfg: cfg.require_s3m_token("odo"), "odo"),
        (lambda cfg: cfg.require_nersc_iri_token(), "perlmutter"),
        (lambda cfg: cfg.require_globus_token("lux"), "lux"),
    ],
)
def test_a_missing_token_names_its_cluster(ask, cluster):
    with pytest.raises(MissingCredential) as raised:
        ask(UserConfig())

    assert raised.value.cluster == cluster


# ---------------------------------------------------------------------------
# The reading a watch polls with
# ---------------------------------------------------------------------------


class _RaisingIri(FakeIriClient):
    def __init__(self, error: Exception):
        super().__init__()
        self.error = error

    async def get_job_status(self, job_id: str) -> dict:
        raise self.error


@pytest.fixture
def no_globus(monkeypatch):
    """A watch's reading never touches Globus, whatever the job's state."""

    def refuse(**kwargs):
        raise AssertionError("a state-only reading built a Globus client")

    monkeypatch.setattr(m, "create_globus_client", refuse)


async def test_the_state_of_a_running_job_is_read_from_iri_alone(
    monkeypatch, no_globus
):
    iri = FakeIriClient(status={"state": "RUNNING"})
    monkeypatch.setattr(m, "client_for", lambda cluster, token: iri)

    state = await m._read_job_state(
        UserConfig(frontier_s3m_token="t"), "123", "frontier"
    )

    assert state == {"state": "RUNNING"}


async def test_a_finished_job_reads_with_its_exit_code(monkeypatch, no_globus):
    iri = FakeIriClient(status={"state": "FAILED", "exit_code": 7})
    monkeypatch.setattr(m, "client_for", lambda cluster, token: iri)

    state = await m._read_job_state(
        UserConfig(nersc_iri_token="t"), "123", "perlmutter"
    )

    assert state == {"state": "FAILED", "exit_code": 7}


async def test_a_missing_token_reads_as_lost_access(no_globus):
    state = await m._read_job_state(UserConfig(), "123", "frontier")

    assert state["error"] == "access_lost"
    assert state["cluster"] == "frontier"
    assert "No S3M token configured" in state["message"]


async def test_a_refused_token_reads_as_lost_access(monkeypatch, no_globus):
    iri = _RaisingIri(IriCredentialRejected("rejected", cluster="odo"))
    monkeypatch.setattr(m, "client_for", lambda cluster, token: iri)

    state = await m._read_job_state(UserConfig(odo_s3m_token="t"), "123", "odo")

    assert (state["error"], state["cluster"]) == ("access_lost", "odo")


async def test_a_429_reads_as_rate_limited_with_its_wait(monkeypatch, no_globus):
    iri = _RaisingIri(IriRateLimited("slow down", retry_after=120))
    monkeypatch.setattr(m, "client_for", lambda cluster, token: iri)

    state = await m._read_job_state(UserConfig(lux_s3m_token="t"), "123", "lux")

    assert state == {
        "error": "rate_limited",
        "retry_after": 120,
        "message": "slow down",
    }


async def test_any_other_failure_reads_as_failed(monkeypatch, no_globus):
    iri = _RaisingIri(IriError("Job status on frontier failed: HTTP 502"))
    monkeypatch.setattr(m, "client_for", lambda cluster, token: iri)

    state = await m._read_job_state(
        UserConfig(frontier_s3m_token="t"), "123", "frontier"
    )

    assert state == {
        "error": "failed",
        "message": "Job status on frontier failed: HTTP 502",
    }


async def test_a_dry_run_job_reads_from_its_synthetic_queue(monkeypatch, no_globus):
    monkeypatch.setattr(m.settings, "hpc_queue_delay_s", 0)
    job_id = dry_run.record_submit("frontier", "example", 1, 60)

    state = await m._read_job_state(UserConfig(), job_id, "frontier")

    assert state == {"state": "COMPLETED", "exit_code": 0}


# ---------------------------------------------------------------------------
# What the agent reads
# ---------------------------------------------------------------------------


async def test_get_hpc_job_status_raises_the_typed_errors(monkeypatch, tmp_path):
    with pytest.raises(MissingCredential):
        await m._get_job_status(
            UserConfig(frontier_remote_dir="/fake/vista"),
            tmp_path,
            "123",
            cluster="frontier",
        )

    iri = _RaisingIri(
        IriRateLimited("Job status on frontier: too many requests", retry_after=60)
    )
    monkeypatch.setattr(m, "client_for", lambda cluster, token: iri)
    with pytest.raises(IriRateLimited, match="too many requests"):
        await m._get_job_status(
            UserConfig(frontier_s3m_token="t", frontier_remote_dir="/fake/vista"),
            tmp_path,
            "123",
            cluster="frontier",
        )
