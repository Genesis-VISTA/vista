"""
Per-job OLCF accounts: a job may charge its own project (cluster_defaults.json
`account`) instead of the cluster's; the user's S3M token is then verified
against THAT project, and the account is remembered so status and outputs
verify against it too. No bundled job overrides the account today, so every
default path must behave exactly as before.
"""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

import vista_mcp_server.submit_job_mcp as m
from vista_mcp_server.config import settings
from vista_mcp_server.lib.user_config import UserConfig
from fakes import FakeIriClient

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture
def checked(monkeypatch):
    """Record (token, expected_project, cluster) for every introspection check."""
    calls: list[tuple[str, str, str]] = []

    async def fake(token, expected_project, *, cluster, introspect_url):
        calls.append((token, expected_project, cluster))

    monkeypatch.setattr(m, "require_s3m_project", fake)
    monkeypatch.setattr(settings, "frontier_account", "chm243")
    monkeypatch.setattr(settings, "odo_account", "gen150-vista")
    return calls


async def test_default_account_is_the_clusters(checked):
    token = await m._require_olcf_access(
        UserConfig(frontier_s3m_token="tok"), "frontier"
    )
    assert token == "tok"
    assert checked == [("tok", "chm243", "frontier")]


async def test_job_account_overrides_the_clusters(checked):
    await m._require_olcf_access(
        UserConfig(frontier_s3m_token="tok"), "frontier", "stf218"
    )
    assert checked == [("tok", "stf218", "frontier")]


async def test_odo_is_unchanged(checked):
    await m._require_olcf_access(UserConfig(odo_s3m_token="o"), "odo")
    assert checked == [("o", "gen150-vista", "odo")]


async def test_no_token_is_refused_before_introspection(checked):
    with pytest.raises(ToolError, match="No S3M token configured"):
        await m._require_olcf_access(UserConfig(), "frontier")
    assert checked == []


def test_account_survives_the_registry_round_trip():
    jobs = {"9": m.SubmittedJob(cluster="frontier", output_dir="/o", account="stf218")}
    assert m._deserialize_jobs(m._serialize_jobs(jobs))["9"].account == "stf218"
    # Entries written before accounts were recorded load as the cluster default.
    old = m._deserialize_jobs({"8": {"cluster": "frontier", "log_path": None}})
    assert old["8"].account is None


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(
        m,
        "_submitted_jobs",
        {
            "10": m.SubmittedJob(
                cluster="frontier", output_dir="/o/10", account="stf218"
            ),
            "11": m.SubmittedJob(cluster="frontier", output_dir="/o/11"),
        },
    )


@pytest.mark.parametrize(("job_id", "account"), [("10", "stf218"), ("11", None)])
async def test_status_verifies_the_jobs_account(
    registry, monkeypatch, tmp_path, job_id, account
):
    seen = {}

    async def access(cfg, cluster, acct=None):
        seen["account"] = acct
        return "tok"

    async def iri_for(cluster, cfg, s3m_token=None):
        seen["token"] = s3m_token
        return FakeIriClient(status={"state": "QUEUED"})

    monkeypatch.setattr(m, "_require_olcf_access", access)
    monkeypatch.setattr(m, "_create_olcf_iri_for", iri_for)
    text = await m._get_olcf_job_status(
        UserConfig(), tmp_path, job_id, cluster="frontier"
    )
    assert "STATE=QUEUED" in text
    assert seen == {"account": account, "token": "tok"}


async def test_outputs_verify_the_jobs_account(registry, monkeypatch, tmp_path):
    seen = {}

    async def access(cfg, cluster, acct=None):
        seen["account"] = acct
        raise ToolError("stop here")

    monkeypatch.setattr(m, "_require_olcf_access", access)
    with pytest.raises(ToolError, match="stop here"):
        await m._get_olcf_job_outputs(
            UserConfig(), tmp_path, "10", ["a.txt"], cluster="frontier"
        )
    assert seen["account"] == "stf218"


def test_no_bundled_job_overrides_the_frontier_account():
    for name, info in m.AVAILABLE_JOBS.items():
        if info.cluster_defaults.frontier is not None:
            assert info.cluster_defaults.frontier.account is None, name
