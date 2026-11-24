"""
What a hosted deployment reports and refuses (openspec change
`multi-user-hosted`, spec `hosted-mode`): the deployment on `GET /version`,
and no in-app updates, NERSC IRI, Custom inference provider, or skill import
from anywhere but an allowed host. Desktop keeps every one.
"""

import pytest

from vista_backend.agents import skill_repo
from vista_backend.config import settings


_signed_in: dict[str, str] = {}
""" The token every `_request` carries while `hosted` is in effect. """


@pytest.fixture
def hosted(hosted):
    """
    conftest's hosted deployment (a fake realm the backend trusts), signed in:
    every route needs a user when hosted, so the refusals below are reached only
    with a token.
    """
    from harness.tokens import bearer

    _signed_in.update(bearer(hosted.token()))
    yield hosted
    _signed_in.clear()


async def _request(session, method, path, **kwargs):
    from harness import api_client

    headers = {**_signed_in, **kwargs.pop("headers", {})}
    with api_client(session) as (client, _):
        return await client.request(method, path, headers=headers, **kwargs)


# ---------------------------------------------------------------------------
# The flag, reported at runtime
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("deployment", ["desktop", "hosted"])
async def test_the_version_reports_the_deployment(session, monkeypatch, deployment):
    monkeypatch.setattr(settings, "deployment", deployment)
    monkeypatch.setattr(settings, "sign_out_url", "https://gw.example/logout")
    monkeypatch.setattr(settings, "version", "0.2.5")

    got = (await _request(session, "GET", "/version")).json()
    assert got["deployment"] == deployment
    # The sign-out link is the gateway's, so only a hosted VISTA reports it.
    assert got["sign_out_url"] == (
        "https://gw.example/logout" if deployment == "hosted" else None
    )
    # A hosted VISTA is updated by its operator, never from the page.
    assert got["checks"] == ("off" if deployment == "hosted" else "on")


# ---------------------------------------------------------------------------
# Updates
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hosted_refuses_an_update_check_and_install(session, hosted):
    check = await _request(session, "POST", "/updates/check")
    install = await _request(
        session, "POST", "/updates/download", json={"version": "9.9.9"}
    )
    assert check.status_code == 403
    assert install.status_code == 403
    assert "hosted" in install.json()["detail"]


@pytest.mark.anyio
async def test_desktop_still_checks_for_updates(session, monkeypatch):
    monkeypatch.setattr(settings, "version", "dev")
    assert (await _request(session, "POST", "/updates/check")).status_code == 200
    # No update on offer: the desktop answer, not a refusal.
    got = await _request(session, "POST", "/updates/download", json={"version": "9"})
    assert got.status_code == 409


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        {"nersc_account": "m1234"},
        {"nersc_remote_dir": "/pscratch/x"},
        {"nersc_iri_token": "tok"},
        {"inference_provider": "custom"},
        {"inference_base_url": "https://llm.example/v1"},
        {"inference_custom_api_key": "sk-x"},
    ],
)
async def test_hosted_refuses_nersc_and_custom_provider(session, hosted, body):
    got = await _request(session, "PUT", "/settings", json=body)
    assert got.status_code == 403, got.text


@pytest.mark.anyio
async def test_hosted_still_saves_other_settings_and_accepts_clearing(session, hosted):
    got = await _request(
        session,
        "PUT",
        "/settings",
        json={
            "display_name": "Ada",
            "nersc_account": None,
            "inference_provider": "mag",
        },
    )
    assert got.status_code == 200, got.text


@pytest.mark.anyio
async def test_hosted_does_not_offer_the_custom_provider(session, hosted):
    got = await _request(session, "GET", "/settings/inference")
    assert "custom" not in [p["id"] for p in got.json()["providers"]]


@pytest.mark.anyio
async def test_desktop_saves_nersc_and_custom(session):
    for body in ({"nersc_account": "m1234"}, {"inference_provider": "custom"}):
        got = await _request(session, "PUT", "/settings", json=body)
        assert got.status_code == 200, got.text


# ---------------------------------------------------------------------------
# Skill import
# ---------------------------------------------------------------------------


@pytest.fixture
def dns(monkeypatch):
    """A fake resolver: `table[host]` is what that name resolves to."""
    table: dict[str, list[str]] = {}

    def resolve(host: str) -> list[str]:
        return table[host]

    monkeypatch.setattr(skill_repo, "_resolve", resolve)
    return table


@pytest.fixture
def allowed(monkeypatch, hosted):
    monkeypatch.setattr(
        settings,
        "skill_import_allowed_hosts",
        ["github.com", "git.lab.test", "10.0.0.5"],
    )


def test_hosted_allows_an_allowed_host_that_resolves_publicly(allowed, dns):
    dns["github.com"] = ["140.82.112.3"]
    skill_repo.check_import_target("https://github.com/o/r")


def test_hosted_refuses_a_host_off_the_list(allowed, dns):
    dns["evil.test"] = ["93.184.216.34"]
    with pytest.raises(skill_repo.SkillRepoError, match="not an allowed"):
        skill_repo.check_import_target("https://evil.test/skill.tar.gz")


def test_hosted_refuses_http(allowed, dns):
    with pytest.raises(skill_repo.SkillRepoError, match="https"):
        skill_repo.check_import_target("http://github.com/o/r")


def test_hosted_refuses_a_private_address_literal_with_the_reason(allowed, dns):
    # Even one on the list: an address is judged by what it is.
    with pytest.raises(skill_repo.SkillRepoError, match="private"):
        skill_repo.check_import_target("https://10.0.0.5/skill.tar.gz")


def test_hosted_refuses_a_literal_off_the_list_too(allowed, dns):
    with pytest.raises(skill_repo.SkillRepoError, match="private"):
        skill_repo.check_import_target("https://169.254.169.254/latest/meta-data")


@pytest.mark.parametrize(
    "addr",
    [
        "127.0.0.1",
        "::1",
        "10.1.2.3",
        "172.16.0.9",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "fe80::1",
        "fd00::1",
        "::ffff:127.0.0.1",
        "0.0.0.0",
    ],
)
def test_hosted_refuses_an_allowed_name_that_resolves_inside(allowed, dns, addr):
    dns["git.lab.test"] = ["140.82.112.3", addr]  # one bad answer is enough
    with pytest.raises(skill_repo.SkillRepoError, match="resolves to"):
        skill_repo.check_import_target("https://git.lab.test/g/r")


def test_hosted_checks_every_redirect(allowed, dns):
    dns["git.lab.test"] = ["10.9.9.9"]
    with pytest.raises(skill_repo.SkillRepoError, match="resolves to"):
        skill_repo._GuardedRedirects().redirect_request(
            None, None, 302, "Found", {}, "https://git.lab.test/elsewhere"
        )


def test_hosted_refuses_before_any_request(allowed, dns, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a request was made")

    monkeypatch.setattr(skill_repo._opener, "open", boom)
    with pytest.raises(skill_repo.SkillRepoError):
        skill_repo._open("https://10.0.0.5/skill.tar.gz", None, None)


def test_hosted_with_no_allowed_hosts_allows_nothing(hosted, dns, monkeypatch):
    monkeypatch.setattr(settings, "skill_import_allowed_hosts", [])
    dns["github.com"] = ["140.82.112.3"]
    with pytest.raises(skill_repo.SkillRepoError, match="not an allowed"):
        skill_repo.check_import_target("https://github.com/o/r")


def test_desktop_accepts_any_https_host(monkeypatch, dns):
    monkeypatch.setattr(settings, "deployment", "desktop")
    skill_repo.check_import_target("https://10.0.0.5/skill.tar.gz")  # no resolver call


def test_allowed_hosts_are_read_from_a_comma_separated_variable(monkeypatch):
    from vista_backend.config import Settings

    monkeypatch.setenv(
        "VISTA_BACKEND_SKILL_IMPORT_ALLOWED_HOSTS", " GitHub.com, git.lab.test ,"
    )
    assert Settings().skill_import_allowed_hosts == ["github.com", "git.lab.test"]
