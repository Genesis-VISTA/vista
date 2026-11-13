"""The HPC availability checks behind the NavRail cards, with no network.

Facility, S3M, and IRI responses are faked with `httpx.MockTransport`, shaped
after what the live endpoints returned in the hpc-cards spike (2026-09-25) and,
for S3M's v2 feed, on 2026-11-07 (rows named by resource id, Lux listed before
Frontier, an empty list for no incidents); Globus is faked at the probe. Every scenario in the `hpc-availability` spec's
check and state requirements has a case here.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import requests
import globus_sdk
from globus_sdk import AuthAPIError, TransferAPIError

from vista_backend.config import HpcClusterSettings
from vista_backend.db.schemas import LocalSettingsPublic
from vista_backend.services import hpc_status as hs
from vista_backend.services.hpc_status import (
    Check,
    ClusterChecks,
    GlobusCheckFailed,
    GlobusSessionExpired,
    HpcStatusService,
    globus_source,
    resolve_state,
)

NOW = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)
S = HpcClusterSettings.model_validate({})
ODO_PROJECT = "abc123"
FRONTIER_PROJECT = "xyz789"
LUX_PROJECT = "lmn456"
""" Projects no deployment is configured for: any project's token works. """
ODO = "https://amsc-open.s3m.olcf.ornl.gov"
FRONTIER = "https://amsc-moderate.s3m.olcf.ornl.gov"
NERSC = "https://api.iri.nersc.gov"
INTROSPECT = "https://s3m.olcf.ornl.gov/olcf/v1/token/ctls/introspect"
API = {ODO: "/api/v2", FRONTIER: "/api/v2", NERSC: "/api/v1"}
""" Each facility at the version its clusters are configured for. """
PERLMUTTER_ID = S.perlmutter_iri_resource_id


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# A fake set of facilities
# ---------------------------------------------------------------------------


class Facilities:
    """Routes requests to canned answers and records what was asked."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.status = {"odo": "up", "frontier": "up", "perlmutter": "up", "lux": "up"}
        self.incidents: dict[str, list[dict]] = {ODO: [], FRONTIER: [], NERSC: []}
        self.down: set[str] = set()
        # token -> (introspect status, project, extra claims)
        self.s3m: dict[str, tuple[int, str | None, dict]] = {}
        # (base, token) -> status for compute/resources
        self.iri: dict[tuple[str, str], int] = {}

    def resources(self, base: str) -> list[dict]:
        if base == ODO:
            return [
                {
                    "id": "odo",
                    "name": "Odo",
                    "group": "olcf",
                    "current_status": self.status["odo"],
                },
                {
                    "id": "defiant",
                    "name": "Defiant",
                    "group": "olcf",
                    "current_status": "down",
                },
            ]
        if base == FRONTIER:
            # Lux first, as the moderate enclave lists them.
            return [
                {
                    "id": "lux",
                    "name": "Lux",
                    "group": "olcf",
                    "current_status": self.status["lux"],
                },
                {
                    "id": "frontier",
                    "name": "Frontier",
                    "group": "olcf",
                    "current_status": self.status["frontier"],
                },
            ]
        return [
            {
                "id": "s",
                "name": "scratch",
                "group": "perlmutter",
                "current_status": "up",
            },
            {
                "id": PERLMUTTER_ID,
                "name": "compute",
                "group": "perlmutter",
                "current_status": self.status["perlmutter"],
            },
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        url = str(request.url)
        base = next((b for b in (ODO, FRONTIER, NERSC) if url.startswith(b)), None)
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if base in self.down:
            raise httpx.ConnectTimeout("down", request=request)
        if url == INTROSPECT:
            status, project, extra = self.s3m.get(token, (401, None, {}))
            if status != 200:
                return httpx.Response(
                    status, json={"code": 16, "message": "unauthorized"}
                )
            return httpx.Response(
                200,
                json={
                    "token": {
                        "project": project,
                        "plannedExpiration": "2026-09-26T14:45:38.756330Z",
                        "securityEnclave": "open",
                        **extra,
                    }
                },
            )
        assert base, url
        path = request.url.path
        # Any other version's path falls through to a 404.
        api = API[base]
        if path == f"{api}/status/resources":
            return httpx.Response(200, json=self.resources(base))
        if path == f"{api}/status/incidents":
            # Only incidents active now are asked for; unfiltered, NERSC
            # returns its oldest hundred.
            assert request.url.params.get("time") == "2026-09-25T15:00:00Z"
            if not self.incidents[base] and api == "/api/v1":
                # What OLCF's v1 answered when nothing was active; v2 answers [].
                return httpx.Response(404, json={"detail": "No incidents found"})
            return httpx.Response(200, json=self.incidents[base])
        if path == f"{api}/compute/resources":
            status = self.iri.get((base, token), 401)
            return httpx.Response(status, json=[] if status == 200 else {"code": 16})
        return httpx.Response(404)

    def calls_to(self, base: str) -> int:
        return sum(str(r.url).startswith(base) for r in self.calls)


class FakeGlobus:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.result: Exception | None = None

    def __call__(self, **kw) -> None:
        self.calls.append(kw)
        if self.result:
            raise self.result


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make_service(
    fac: Facilities,
    globus: FakeGlobus | None = None,
    *,
    settings=S,
    clock: Clock | None = None,
) -> HpcStatusService:
    return HpcStatusService(
        settings=settings,
        transport=httpx.MockTransport(fac.handler),
        globus_probe=globus or FakeGlobus(),
        now=lambda: NOW,
        monotonic=clock or Clock(),
    )


SETTINGS = dict(
    odo_remote_dir="/gpfs/wolf2/olcf/abc123/proj-shared/vista",
    frontier_remote_dir="/lustre/orion/abc123/proj-shared/vista",
    nersc_remote_dir="/pscratch/sd/r/r/.vista",
    nersc_account="m1234",
    lux_remote_dir="/lustre/orion/abc123/proj-shared/vista-lux",
)
""" Everything a submission needs besides credentials. `local` sets it unless a test overrides it. """


def local(**fields) -> LocalSettingsPublic:
    return LocalSettingsPublic(install_id=uuid.uuid4(), **{**SETTINGS, **fields})


CONNECTED = dict(
    odo_s3m_token="odo-tok",
    frontier_s3m_token="fr-tok",
    lux_s3m_token="lux-tok",
    nersc_iri_token="pm-tok",
    odo_globus_token="odo-gt",
    odo_globus_https_token="odo-gh",
    frontier_globus_token="fr-gt",
    frontier_globus_https_token="fr-gh",
)


def healthy() -> Facilities:
    fac = Facilities()
    fac.s3m = {
        "odo-tok": (200, ODO_PROJECT, {}),
        "fr-tok": (200, FRONTIER_PROJECT, {}),
        "lux-tok": (200, LUX_PROJECT, {}),
    }
    fac.iri = {
        (ODO, "odo-tok"): 200,
        (FRONTIER, "fr-tok"): 200,
        (FRONTIER, "lux-tok"): 200,
        (NERSC, "pm-tok"): 200,
    }
    return fac


async def statuses(service, u, **kw) -> dict:
    result = await service.status(u, **kw)
    return {c.cluster: c for c in result.clusters}


# ---------------------------------------------------------------------------
# State resolution (spec: Cluster state)
# ---------------------------------------------------------------------------

OK = Check(ok=True, message="ok")


def fail(reason) -> Check:
    return Check(ok=False, reason=reason, message=reason)


@pytest.mark.parametrize(
    ("facility", "credential", "globus", "state"),
    [
        (OK, OK, OK, "ready"),
        (OK, OK, None, "ready"),  # Perlmutter: no Globus check
        (fail("degraded"), fail("rejected"), OK, "degraded"),
        (fail("degraded"), fail("unverifiable"), OK, "degraded"),
        (fail("unreachable"), OK, OK, "unverifiable"),
        (OK, fail("unverifiable"), OK, "unverifiable"),
        (OK, OK, fail("unverifiable"), "unverifiable"),
        (OK, fail("not_connected"), fail("not_connected"), "not_connected"),
        (OK, fail("rejected"), OK, "rejected"),
        (OK, fail("not_active"), OK, "rejected"),
        (OK, OK, fail("not_connected"), "globus_not_connected"),
        (OK, OK, fail("session_expired"), "globus_session_expired"),
    ],
)
def test_resolve_state(facility, credential, globus, state):
    checks = ClusterChecks(
        facility=facility, credential=credential, globus=globus, settings=OK
    )
    assert resolve_state(checks) == state


@pytest.mark.parametrize(
    ("facility", "credential", "settings", "state"),
    [
        (OK, OK, fail("not_connected"), "not_connected"),
        (OK, OK, fail("invalid"), "not_connected"),
        (OK, fail("rejected"), fail("not_connected"), "not_connected"),
        (fail("degraded"), OK, fail("not_connected"), "degraded"),
    ],
)
def test_missing_settings_are_not_connected(facility, credential, settings, state):
    checks = ClusterChecks(
        facility=facility, credential=credential, globus=OK, settings=settings
    )
    assert resolve_state(checks) == state


@pytest.mark.parametrize(
    ("cluster", "fields", "reason", "says"),
    [
        (
            "odo",
            {"odo_remote_dir": None},
            "not_connected",
            "No Odo remote directory is set.",
        ),
        (
            "frontier",
            {"frontier_remote_dir": ""},
            "not_connected",
            "No Frontier remote directory",
        ),
        (
            "perlmutter",
            {"nersc_account": None, "nersc_remote_dir": None},
            "not_connected",
            "No NERSC account or Perlmutter remote directory is set.",
        ),
        (
            "lux",
            {"lux_remote_dir": None},
            "not_connected",
            "No Lux remote directory is set.",
        ),
        (
            "odo",
            {"odo_remote_dir": "/gpfs/wolf2/olcf/<project>/proj-shared/vista"},
            "invalid",
            "Replace any <project>",
        ),
        ("frontier", {"frontier_remote_dir": "vista"}, "invalid", "absolute path"),
        ("odo", {"odo_remote_dir": "/"}, "invalid", "other than /"),
    ],
)
def test_settings_check_names_what_is_missing(cluster, fields, reason, says):
    check = hs.settings_check(cluster, local(**fields))
    assert not check.ok
    assert check.reason == reason
    assert says in check.message


def test_settings_check_passes_with_everything_set():
    u = local()
    assert hs.settings_check("odo", u) == Check(
        ok=True, message=SETTINGS["odo_remote_dir"]
    )
    # Lux, like Odo and Frontier, charges its token's project: no account setting.
    assert hs.settings_check("lux", u) == Check(
        ok=True, message=SETTINGS["lux_remote_dir"]
    )


@pytest.mark.anyio
async def test_card_is_not_connected_without_a_remote_directory_even_when_cached():
    """
    A token and Globus alone are not enough: every job would be refused. And
    setting the folder updates the card at once, though the facility checks
    are cached.
    """
    fac = healthy()
    service = make_service(fac)
    u = local(**CONNECTED, frontier_remote_dir=None)
    first = (await statuses(service, u))["frontier"]
    assert first.state == "not_connected"
    assert first.checks.credential.ok and first.checks.globus.ok
    assert first.checks.settings.message == "No Frontier remote directory is set."

    calls = len(fac.calls)
    fixed = u.model_copy(
        update={"frontier_remote_dir": SETTINGS["frontier_remote_dir"]}
    )
    second = (await statuses(service, fixed))["frontier"]
    assert second.state == "ready"
    assert len(fac.calls) == calls  # served from the cache, with the new settings


# ---------------------------------------------------------------------------
# Facility check
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_all_ready():
    fac, globus = healthy(), FakeGlobus()
    result = await statuses(make_service(fac, globus), local(**CONNECTED))
    assert list(result) == ["frontier", "odo", "perlmutter", "lux"]
    assert {c: s.state for c, s in result.items()} == dict.fromkeys(result, "ready")
    assert result["perlmutter"].checks.globus is None
    assert result["odo"].checks.credential.expires_at == datetime(
        2026, 9, 26, 14, 45, 38, 756330, tzinfo=timezone.utc
    )
    assert result["odo"].checked_at == NOW


@pytest.mark.anyio
async def test_resolved_incident_is_ignored():
    fac = healthy()
    fac.incidents[FRONTIER] = [
        {
            "name": "File system hardware maintenance",
            "status": "down",
            "start": "2026-09-15T12:00:00Z",
            "end": "2026-09-16T19:12:00Z",
            "resolution": "completed",
            "resource_uris": [f"{FRONTIER}/api/v2/status/resources/frontier"],
        }
    ]
    result = await statuses(make_service(fac), local(**CONNECTED))
    assert result["frontier"].state == "ready"


@pytest.mark.anyio
async def test_open_incident_is_degraded():
    fac = healthy()
    fac.incidents[FRONTIER] = [
        {
            "name": "Lustre maintenance",
            "start": iso(NOW - timedelta(hours=1)),
            "end": iso(NOW + timedelta(hours=3)),
            "resource_uris": [f"{FRONTIER}/api/v2/status/resources/frontier"],
        }
    ]
    frontier = (await statuses(make_service(fac), local(**CONNECTED)))["frontier"]
    assert frontier.state == "degraded"
    assert frontier.checks.facility.incident.name == "Lustre maintenance"


@pytest.mark.anyio
async def test_future_incident_is_not_open_yet():
    fac = healthy()
    fac.incidents[FRONTIER] = [
        {
            "name": "Next week",
            "start": iso(NOW + timedelta(days=7)),
            "end": None,
            "resource_uris": [f"{FRONTIER}/api/v2/status/resources/frontier"],
        }
    ]
    assert (await statuses(make_service(fac), local(**CONNECTED)))[
        "frontier"
    ].state == "ready"


@pytest.mark.anyio
async def test_resource_down_outranks_a_rejected_token():
    fac = healthy()
    fac.status["odo"] = "down"
    fac.iri[(ODO, "odo-tok")] = 401
    odo = (await statuses(make_service(fac), local(**CONNECTED)))["odo"]
    assert odo.state == "degraded"
    assert "down" in odo.checks.facility.message
    assert odo.checks.credential.reason == "rejected"  # still reported in the details


@pytest.mark.anyio
async def test_unreachable_facility_is_never_ready():
    fac = healthy()
    fac.down.add(NERSC)
    pm = (await statuses(make_service(fac), local(**CONNECTED)))["perlmutter"]
    assert pm.checks.facility.reason == "unreachable"
    assert pm.state == "unverifiable"


@pytest.mark.anyio
async def test_other_odo_resources_do_not_stand_in_for_odo():
    """Defiant is down on the same feed; only Odo's configured id counts."""
    fac = healthy()
    assert (await statuses(make_service(fac), local(**CONNECTED)))[
        "odo"
    ].state == "ready"


@pytest.mark.anyio
async def test_facility_feed_is_shared_across_checks():
    fac = healthy()
    service = make_service(fac)
    await service.status(local(**CONNECTED))
    before = sum("/status/" in str(r.url) for r in fac.calls)
    await service.status(local(**CONNECTED))
    assert sum("/status/" in str(r.url) for r in fac.calls) == before


# ---------------------------------------------------------------------------
# Credential check
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_token_means_not_connected_and_no_authenticated_call():
    fac = healthy()
    result = await statuses(make_service(fac), local())
    assert {s.state for s in result.values()} == {"not_connected"}
    assert not any("authorization" in r.headers for r in fac.calls)
    # The facility is still checked, so the details can say it is up.
    assert result["odo"].checks.facility.ok


@pytest.mark.anyio
async def test_rejected_token():
    fac = healthy()
    fac.iri[(NERSC, "pm-tok")] = 401
    fac.s3m["odo-tok"] = (401, None, {})
    result = await statuses(make_service(fac), local(**CONNECTED))
    assert result["perlmutter"].state == "rejected"
    assert result["odo"].state == "rejected"


@pytest.mark.anyio
async def test_token_for_any_project_is_reported_not_judged():
    """No project is configured: whatever the token belongs to is its jobs'
    account, and the card says which."""
    fac = healthy()
    fac.s3m["odo-tok"] = (200, "zzz999", {})
    odo = (await statuses(make_service(fac), local(**CONNECTED)))["odo"]
    assert odo.state == "ready"
    assert odo.checks.credential.project == "zzz999"


@pytest.mark.anyio
async def test_token_for_the_other_enclave_is_rejected_by_iri():
    """An Odo token pasted into Frontier: Frontier's IRI refuses it, and that
    is the answer -- there is no project comparison to make."""
    fac = healthy()
    fac.s3m["fr-tok"] = (200, ODO_PROJECT, {})
    fac.iri[(FRONTIER, "fr-tok")] = 401  # what Frontier's IRI really answers
    frontier = (await statuses(make_service(fac), local(**CONNECTED)))["frontier"]
    assert frontier.state == "rejected"


@pytest.mark.anyio
async def test_token_not_active_yet():
    fac = healthy()
    start = NOW + timedelta(hours=2)
    fac.s3m["odo-tok"] = (
        200,
        ODO_PROJECT,
        {"delayedStart": True, "delayDate": iso(start)},
    )
    odo = (await statuses(make_service(fac), local(**CONNECTED)))["odo"]
    assert odo.state == "rejected"
    assert odo.checks.credential.reason == "not_active"
    assert odo.checks.credential.active_from == start


@pytest.mark.anyio
async def test_delayed_start_in_the_past_is_fine():
    fac = healthy()
    fac.s3m["odo-tok"] = (
        200,
        ODO_PROJECT,
        {"delayedStart": True, "delayDate": iso(NOW - timedelta(hours=2))},
    )
    assert (await statuses(make_service(fac), local(**CONNECTED)))[
        "odo"
    ].state == "ready"


@pytest.mark.anyio
@pytest.mark.parametrize("status", [403, 500, 502])
async def test_unexpected_iri_answer_is_unverifiable(status):
    fac = healthy()
    fac.iri[(NERSC, "pm-tok")] = status
    pm = (await statuses(make_service(fac), local(**CONNECTED)))["perlmutter"]
    assert pm.state == "unverifiable"
    assert pm.checks.credential.http_status == status


# ---------------------------------------------------------------------------
# Globus check
# ---------------------------------------------------------------------------


def test_globus_source_order():
    own = local(
        odo_globus_token="o-gt",
        odo_globus_https_token="o-gh",
        globus_token="s-gt",
        globus_https_token="s-gh",
    )
    assert globus_source("odo", own).transfer == "o-gt"
    shared = local(globus_token="s-gt", globus_https_token="s-gh")
    assert globus_source("odo", shared).transfer == "s-gt"
    # Half a pair does not count, and there is nothing after the shared pair.
    half = local(odo_globus_token="o-gt")
    assert globus_source("odo", half) is None
    half_and_shared = local(
        odo_globus_token="o-gt", globus_token="s-gt", globus_https_token="s-gh"
    )
    assert globus_source("odo", half_and_shared).transfer == "s-gt"
    assert globus_source("frontier", half) is None


@pytest.mark.anyio
async def test_deployment_globus_variables_do_not_count(monkeypatch):
    """The variables that used to be a deployment-wide Globus login are
    ignored: a researcher who connected nothing is not connected."""
    monkeypatch.setenv("VISTA_MCP_ODO_GLOBUS_REFRESH_TOKEN", "dep-gt")
    monkeypatch.setenv("VISTA_MCP_ODO_GLOBUS_HTTPS_REFRESH_TOKEN", "dep-gh")
    fac, globus = healthy(), FakeGlobus()
    settings = HpcClusterSettings()
    u = local(odo_s3m_token="odo-tok")
    odo = (await statuses(make_service(fac, globus, settings=settings), u))["odo"]
    assert odo.state == "globus_not_connected"
    assert globus.calls == []


@pytest.mark.anyio
async def test_globus_not_connected():
    odo = (await statuses(make_service(healthy()), local(odo_s3m_token="odo-tok")))[
        "odo"
    ]
    assert odo.state == "globus_not_connected"


@pytest.mark.anyio
async def test_globus_session_expired():
    globus = FakeGlobus()
    globus.result = GlobusSessionExpired("401")
    odo = (await statuses(make_service(healthy(), globus), local(**CONNECTED)))["odo"]
    assert odo.state == "globus_session_expired"


@pytest.mark.anyio
async def test_globus_failure_of_another_kind_is_unverifiable():
    globus = FakeGlobus()
    globus.result = RuntimeError("network")
    odo = (await statuses(make_service(healthy(), globus), local(**CONNECTED)))["odo"]
    assert odo.state == "unverifiable"


NETWORK_ERROR = globus_sdk.NetworkError(
    "Network Error", requests.ConnectionError("refused")
)


async def _odo_globus(result: Exception, **kw):
    globus = FakeGlobus()
    globus.result = result
    odo = (await statuses(make_service(healthy(), globus), local(**CONNECTED), **kw))[
        "odo"
    ]
    return odo


@pytest.mark.anyio
async def test_globus_expired_card_names_the_login():
    odo = await _odo_globus(
        GlobusSessionExpired(
            "The Globus session for Odo has expired: Globus needs a fresh login "
            "with opensso.ccs.ornl.gov. Connect Globus again.",
            http_status=502,
        )
    )
    assert odo.state == "globus_session_expired"
    assert odo.checks.globus.reason == "session_expired"
    assert "opensso.ccs.ornl.gov" in odo.checks.globus.message
    assert odo.checks.globus.http_status == 502


@pytest.mark.anyio
async def test_globus_503_is_unverifiable_with_the_status():
    odo = await _odo_globus(
        GlobusCheckFailed(
            "Globus answered HTTP 503 (ServiceUnavailable): try later.",
            http_status=503,
        )
    )
    assert odo.state == "unverifiable"
    assert "503" in odo.checks.globus.message
    assert odo.checks.globus.http_status == 503


@pytest.mark.anyio
async def test_globus_timeout_is_unverifiable_with_the_limit():
    odo = await _odo_globus(TimeoutError())
    assert odo.state == "unverifiable"
    assert odo.checks.globus.message == "Globus did not answer within 10 s"


@pytest.mark.anyio
async def test_globus_network_error_is_unverifiable():
    odo = await _odo_globus(NETWORK_ERROR)
    assert odo.state == "unverifiable"
    assert odo.checks.globus.message == "Globus could not be reached"


@pytest.mark.anyio
async def test_globus_failure_is_logged_with_code_and_body_but_no_token(caplog):
    with caplog.at_level("INFO", logger="vista_backend.services.hpc_status"):
        await _odo_globus(
            GlobusCheckFailed(
                "d",
                log="HTTP 503 code=ServiceUnavailable body=try later",
                http_status=503,
            )
        )
    assert "Globus check failed" in caplog.text
    assert "ServiceUnavailable" in caplog.text
    assert "try later" in caplog.text
    assert "odo-gt" not in caplog.text
    assert "odo-gh" not in caplog.text


def _globus_error(cls, status: int, code: str, url: str, *, body=None):
    response = requests.Response()
    response.status_code = status
    if body is None:
        response._content = b'{"code":"%s","message":"m"}' % code.encode()
        response.headers["Content-Type"] = "application/json"
    elif isinstance(body, bytes):
        response._content = body
        response.headers["Content-Type"] = "text/html"
    else:
        response._content = json.dumps(body).encode()
        response.headers["Content-Type"] = "application/json"
    response.request = requests.Request("GET", url).prepare()
    return cls(response)


TRANSFER_URL = "https://transfer.api.globus.org/v0.10/operation"
AUTH_URL = "https://auth.globus.org/v2/oauth2/token"

# Live bodies from the change's design.md Context.
GARE_502_ODO = {
    "code": "ExternalError.DirListingFailed.LoginFailed",
    "message": "Login Required",
    "request_id": "r-gare",
    "authorization_parameters": {
        "session_message": "Session reauthentication required (Globus Transfer)",
        "session_required_single_domain": [
            "opensso.ccs.ornl.gov",
            "clients.auth.globus.org",
        ],
    },
}
GARE_NO_DOMAIN = {
    "code": "ExternalError.DirListingFailed.LoginFailed",
    "message": "Login Required",
    "request_id": "r-gare",
    "authorization_parameters": {"session_message": "Please log in again"},
}
INVALID_GRANT_400 = {"error": "invalid_grant"}
EXCHANGE_503 = {"code": "ServiceUnavailable", "message": "Auth is overloaded"}
TRANSFER_503_PLAIN = b"<html>Service Unavailable</html>"
TRANSFER_400_PLAIN = {
    "code": "ClientError.BadRequest",
    "message": "bad path",
    "request_id": "r-400",
}


def _patch_globus_sdk(monkeypatch, *, refresh_error=None, ls_error=None):
    calls = []

    class Authorizer:
        def __init__(self, token, auth_client):
            calls.append(("refresh", token))
            if refresh_error:
                raise refresh_error

    class Transfer:
        def __init__(self, authorizer):
            pass

        def operation_ls(self, collection, path, limit):
            calls.append(("ls", collection, path, limit))
            if ls_error:
                raise ls_error

    monkeypatch.setattr(
        hs.globus_sdk, "NativeAppAuthClient", lambda client_id: object()
    )
    monkeypatch.setattr(hs.globus_sdk, "RefreshTokenAuthorizer", Authorizer)
    monkeypatch.setattr(hs.globus_sdk, "TransferClient", Transfer)
    return calls


def _probe(**kw):
    hs.probe_globus(transfer="t", https="h", collection_id="c", client_id="id", **kw)


def test_probe_refreshes_both_tokens_then_lists(monkeypatch):
    calls = _patch_globus_sdk(monkeypatch)
    _probe()
    assert calls == [("refresh", "t"), ("refresh", "h"), ("ls", "c", "/~/", 1)]


def test_probe_refresh_refusal_is_session_expired(monkeypatch):
    err = _globus_error(AuthAPIError, 400, "", AUTH_URL, body=INVALID_GRANT_400)
    _patch_globus_sdk(monkeypatch, refresh_error=err)
    with pytest.raises(GlobusSessionExpired) as exc:
        _probe(title="Frontier")
    assert "no longer accepts the saved" in str(exc.value)
    assert "Frontier" in str(exc.value)


def test_probe_exchange_503_is_check_failed_not_expired(monkeypatch):
    err = _globus_error(AuthAPIError, 503, "", AUTH_URL, body=EXCHANGE_503)
    _patch_globus_sdk(monkeypatch, refresh_error=err)
    with pytest.raises(GlobusCheckFailed) as exc:
        _probe()
    for part in ("503", "ServiceUnavailable", "Auth is overloaded"):
        assert part in str(exc.value)
    assert exc.value.http_status == 503


def test_probe_exchange_400_other_than_invalid_grant_is_check_failed(monkeypatch):
    err = _globus_error(
        AuthAPIError, 400, "", AUTH_URL, body={"error": "invalid_client"}
    )
    _patch_globus_sdk(monkeypatch, refresh_error=err)
    with pytest.raises(GlobusCheckFailed):
        _probe()


def test_probe_transfer_503_without_a_gare_is_check_failed(monkeypatch):
    err = _globus_error(
        TransferAPIError, 503, "", TRANSFER_URL, body=TRANSFER_503_PLAIN
    )
    _patch_globus_sdk(monkeypatch, ls_error=err)
    with pytest.raises(GlobusCheckFailed) as exc:
        _probe()
    assert "503" in str(exc.value)
    assert "None" not in str(exc.value)


def test_probe_transfer_400_with_a_plain_code_is_check_failed(monkeypatch):
    err = _globus_error(
        TransferAPIError, 400, "", TRANSFER_URL, body=TRANSFER_400_PLAIN
    )
    _patch_globus_sdk(monkeypatch, ls_error=err)
    with pytest.raises(GlobusCheckFailed) as exc:
        _probe()
    for part in ("400", "ClientError.BadRequest", "bad path"):
        assert part in str(exc.value)


@pytest.mark.parametrize(
    ("status", "code"), [(401, "AuthenticationFailed"), (403, "ConsentRequired")]
)
def test_probe_lapsed_session_is_session_expired(monkeypatch, status, code):
    err = _globus_error(
        TransferAPIError,
        status,
        code,
        "https://transfer.api.globus.org/v0.10/operation",
    )
    _patch_globus_sdk(monkeypatch, ls_error=err)
    with pytest.raises(GlobusSessionExpired):
        _probe()


@pytest.mark.parametrize(
    ("status", "code"), [(403, "PermissionDenied"), (404, "ClientError.NotFound")]
)
def test_probe_unlistable_home_still_proves_the_session(monkeypatch, status, code):
    err = _globus_error(
        TransferAPIError,
        status,
        code,
        "https://transfer.api.globus.org/v0.10/operation",
    )
    _patch_globus_sdk(monkeypatch, ls_error=err)
    _probe()  # no raise


def test_probe_gare_502_is_session_expired(monkeypatch):
    err = _globus_error(TransferAPIError, 502, "", TRANSFER_URL, body=GARE_502_ODO)
    _patch_globus_sdk(monkeypatch, ls_error=err)
    with pytest.raises(GlobusSessionExpired) as exc:
        _probe(title="Odo")
    detail = str(exc.value)
    assert "opensso.ccs.ornl.gov" in detail
    assert "clients.auth.globus.org" not in detail
    assert "Odo" in detail
    assert "Connect Globus again" in detail
    assert exc.value.http_status == 502


@pytest.mark.parametrize("status", [403, 404])
def test_probe_gare_wins_over_the_403_404_rule(monkeypatch, status):
    err = _globus_error(TransferAPIError, status, "", TRANSFER_URL, body=GARE_502_ODO)
    _patch_globus_sdk(monkeypatch, ls_error=err)
    with pytest.raises(GlobusSessionExpired):
        _probe()


def test_probe_gare_without_a_domain_is_still_session_expired(monkeypatch):
    err = _globus_error(TransferAPIError, 502, "", TRANSFER_URL, body=GARE_NO_DOMAIN)
    _patch_globus_sdk(monkeypatch, ls_error=err)
    with pytest.raises(GlobusSessionExpired) as exc:
        _probe(title="Odo")
    assert "None" not in str(exc.value)
    assert "Connect Globus again" in str(exc.value)


# ---------------------------------------------------------------------------
# The endpoint's contract: caching, visibility, secrecy
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hidden_clusters_make_no_calls():
    fac = healthy()
    u = local(**CONNECTED, hpc_hidden_clusters=["perlmutter"])
    result = await statuses(make_service(fac), u)
    assert "perlmutter" not in result
    assert fac.calls_to(NERSC) == 0


@pytest.mark.anyio
async def test_recent_result_is_reused_and_fresh_reruns():
    fac, clock = healthy(), Clock()
    service = make_service(fac, clock=clock)
    u = local(**CONNECTED)
    await service.status(u)
    n = len(fac.calls)
    await service.status(u)
    assert len(fac.calls) == n  # cached
    await service.status(u, fresh=True)
    assert len(fac.calls) > n
    n = len(fac.calls)
    clock.t += hs.RESULT_TTL + 1
    await service.status(u)
    assert len(fac.calls) > n  # expired


@pytest.mark.anyio
async def test_single_cluster_recheck():
    fac = healthy()
    service = make_service(fac)
    u = local(**CONNECTED)
    await service.status(u)
    before = {b: fac.calls_to(b) for b in (ODO, FRONTIER, NERSC)}
    await service.status(u, fresh=True, cluster="odo")
    assert fac.calls_to(ODO) > before[ODO]
    assert fac.calls_to(FRONTIER) == before[FRONTIER]
    assert fac.calls_to(NERSC) == before[NERSC]


@pytest.mark.anyio
async def test_saving_a_new_token_invalidates_the_cache():
    fac = healthy()
    service = make_service(fac)
    u = local(**CONNECTED)
    fac.iri[(NERSC, "pm-tok")] = 401
    assert (await statuses(service, u))["perlmutter"].state == "rejected"
    fac.iri[(NERSC, "pm-new")] = 200
    u2 = u.model_copy(update={"nersc_iri_token": "pm-new"})
    assert (await statuses(service, u2))["perlmutter"].state == "ready"


@pytest.mark.anyio
async def test_no_secret_ever_appears_in_the_response():
    fac = healthy()
    fac.s3m["fr-tok"] = (200, "other", {})
    fac.iri[(NERSC, "pm-tok")] = 500
    globus = FakeGlobus()
    globus.result = GlobusSessionExpired("expired", log="token odo-gt refused")
    u = local(**CONNECTED)
    body = (await make_service(fac, globus).status(u)).model_dump_json()
    for secret in [*CONNECTED.values(), "Bearer"]:
        assert secret not in body, secret


@pytest.mark.anyio
async def test_route_passes_fresh_and_cluster_through(monkeypatch):
    from vista_backend.api import settings as settings_api

    fac = healthy()
    service = make_service(fac)
    monkeypatch.setattr(hs, "hpc_status_service", service)
    u = local(**CONNECTED)
    first = await settings_api.get_hpc_status(u)
    assert [c.cluster for c in first.clusters] == [
        "frontier",
        "odo",
        "perlmutter",
        "lux",
    ]
    before = fac.calls_to(NERSC)
    await settings_api.get_hpc_status(u, fresh=True, cluster="odo")
    assert fac.calls_to(NERSC) == before


# ---------------------------------------------------------------------------
# Lux: its own v2 resource and S3M token, Frontier's Globus (spec: Facility
# status for every cluster, Credential check for every cluster, Globus check)
# ---------------------------------------------------------------------------

ONLY_LUX = dict(hpc_hidden_clusters=["frontier", "odo", "perlmutter"])


@pytest.mark.anyio
async def test_lux_and_frontier_are_each_their_own_row_on_one_feed():
    """The moderate enclave lists Lux first; neither card reads the other's row."""
    fac = healthy()
    fac.status["lux"] = "down"
    result = await statuses(make_service(fac), local(**CONNECTED))
    assert result["lux"].state == "degraded"
    assert "Lux as down" in result["lux"].checks.facility.message
    assert result["frontier"].state == "ready"

    fac = healthy()
    fac.status["frontier"] = "down"
    result = await statuses(make_service(fac), local(**CONNECTED))
    assert (result["frontier"].state, result["lux"].state) == ("degraded", "ready")


@pytest.mark.anyio
async def test_lux_uses_its_own_token_and_frontiers_globus():
    fac, globus = healthy(), FakeGlobus()
    lux = (await statuses(make_service(fac, globus), local(**CONNECTED, **ONLY_LUX)))[
        "lux"
    ]
    assert lux.state == "ready"
    assert lux.checks.credential.project == LUX_PROJECT
    [probe] = globus.calls
    assert (probe["transfer"], probe["https"]) == ("fr-gt", "fr-gh")
    assert probe["collection_id"] == S.frontier_globus_collection_id
    assert probe["title"] == "Lux"
    authed = [r for r in fac.calls if "authorization" in r.headers]
    assert {
        str(r.url) for r in authed if r.url.path.endswith("/compute/resources")
    } == {f"{FRONTIER}/api/v2/compute/resources"}
    assert {r.headers["authorization"] for r in authed} == {"Bearer lux-tok"}


@pytest.mark.anyio
async def test_a_frontier_token_does_not_connect_lux():
    u = local(**{**CONNECTED, "lux_s3m_token": None}, **ONLY_LUX)
    lux = (await statuses(make_service(healthy()), u))["lux"]
    assert lux.state == "not_connected"
    assert lux.checks.credential.message == "No S3M token is saved for Lux."


@pytest.mark.anyio
async def test_lux_without_frontiers_globus_says_whose_connection_it_needs():
    u = local(
        **{
            **CONNECTED,
            "frontier_globus_token": None,
            "frontier_globus_https_token": None,
        },
        **ONLY_LUX,
    )
    lux = (await statuses(make_service(healthy()), u))["lux"]
    assert lux.state == "globus_not_connected"
    assert lux.checks.globus.message == (
        "Globus file transfer is not connected for Lux (Lux uses Frontier's connection)."
    )


@pytest.mark.anyio
async def test_connecting_frontiers_globus_rechecks_lux():
    """Lux's result is cached by the credentials it uses, Frontier's Globus pair among them."""
    fac, globus = healthy(), FakeGlobus()
    service = make_service(fac, globus)
    u = local(**{**CONNECTED, "frontier_globus_token": None}, **ONLY_LUX)
    assert (await statuses(service, u))["lux"].state == "globus_not_connected"
    connected = u.model_copy(update={"frontier_globus_token": "fr-gt"})
    assert (await statuses(service, connected))["lux"].state == "ready"


@pytest.mark.anyio
async def test_each_facility_is_asked_at_its_clusters_version():
    fac = healthy()
    await statuses(make_service(fac), local(**CONNECTED))
    paths = {
        (str(r.url).split("/api/")[0], r.url.path.split("/")[2])
        for r in fac.calls
        if "/api/" in str(r.url)
    }
    assert paths == {(ODO, "v2"), (FRONTIER, "v2"), (NERSC, "v1")}


@pytest.mark.anyio
async def test_a_resource_id_the_feed_does_not_list_is_unverifiable():
    settings = HpcClusterSettings.model_validate({"lux_iri_resource_id": "lux-renamed"})
    result = await statuses(
        make_service(healthy(), settings=settings), local(**CONNECTED, **ONLY_LUX)
    )
    assert result["lux"].state == "unverifiable"
    assert (
        result["lux"].checks.facility.message
        == "The facility status feed does not list Lux."
    )
