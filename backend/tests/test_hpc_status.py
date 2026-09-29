"""The HPC availability checks behind the NavRail cards, with no network.

Facility, S3M, and IRI responses are faked with `httpx.MockTransport`, shaped
after what the live endpoints returned in the hpc-cards spike (2026-09-25);
Globus is faked at the probe. Every scenario in the `hpc-availability` spec's
check and state requirements has a case here.
"""

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import requests
from globus_sdk import GlobusAPIError, TransferAPIError
from pydantic import SecretStr

from vista_backend.config import HpcClusterSettings
from vista_backend.db.schemas import UserPublicWithConfig
from vista_backend.services import hpc_status as hs
from vista_backend.services.hpc_status import (
    Check,
    ClusterChecks,
    GlobusSessionExpired,
    HpcStatusService,
    globus_source,
    resolve_state,
)

NOW = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)
S = HpcClusterSettings.model_validate({})
ODO = "https://amsc-open.s3m.olcf.ornl.gov"
FRONTIER = "https://amsc-moderate.s3m.olcf.ornl.gov"
NERSC = "https://api.iri.nersc.gov"
INTROSPECT = "https://s3m.olcf.ornl.gov/olcf/v1/token/ctls/introspect"
FRONTIER_ID = "5173cdb4-d82f-5c81-8ef0-598997c12813"


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# A fake set of facilities
# ---------------------------------------------------------------------------


class Facilities:
    """Routes requests to canned answers and records what was asked."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.status = {"odo": "up", "frontier": "up", "perlmutter": "up"}
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
                    "id": S.odo_compute_resource_id,
                    "name": "Odo",
                    "group": "olcf",
                    "current_status": self.status["odo"],
                },
                {
                    "id": "d",
                    "name": "Defiant",
                    "group": "olcf",
                    "current_status": "down",
                },
            ]
        if base == FRONTIER:
            return [
                {
                    "id": FRONTIER_ID,
                    "name": "Frontier",
                    "group": "olcf",
                    "current_status": self.status["frontier"],
                }
            ]
        return [
            {
                "id": "s",
                "name": "scratch",
                "group": "perlmutter",
                "current_status": "up",
            },
            {
                "id": "pm",
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
        if path == "/api/v1/status/resources":
            return httpx.Response(200, json=self.resources(base))
        if path == "/api/v1/status/incidents":
            # Only incidents active now are asked for; unfiltered, NERSC
            # returns its oldest hundred.
            assert request.url.params.get("time") == "2026-09-25T15:00:00Z"
            if not self.incidents[base]:
                # What OLCF really answers when nothing is active.
                return httpx.Response(404, json={"detail": "No incidents found"})
            return httpx.Response(200, json=self.incidents[base])
        if path == "/api/v1/compute/resources":
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


def user(**tokens) -> UserPublicWithConfig:
    return UserPublicWithConfig(id=uuid.uuid4(), email="r@ornl.gov", **tokens)


CONNECTED = dict(
    odo_s3m_token="odo-tok",
    frontier_s3m_token="fr-tok",
    nersc_iri_token="pm-tok",
    odo_globus_token="odo-gt",
    odo_globus_https_token="odo-gh",
    frontier_globus_token="fr-gt",
    frontier_globus_https_token="fr-gh",
)


def healthy() -> Facilities:
    fac = Facilities()
    fac.s3m = {
        "odo-tok": (200, S.odo_account, {}),
        "fr-tok": (200, S.frontier_account, {}),
    }
    fac.iri = {(ODO, "odo-tok"): 200, (FRONTIER, "fr-tok"): 200, (NERSC, "pm-tok"): 200}
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
        (OK, fail("wrong_project"), fail("not_connected"), "wrong_project"),
        (OK, OK, fail("not_connected"), "globus_not_connected"),
        (OK, OK, fail("session_expired"), "globus_session_expired"),
    ],
)
def test_resolve_state(facility, credential, globus, state):
    checks = ClusterChecks(facility=facility, credential=credential, globus=globus)
    assert resolve_state(checks) == state


# ---------------------------------------------------------------------------
# Facility check
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_all_ready():
    fac, globus = healthy(), FakeGlobus()
    result = await statuses(make_service(fac, globus), user(**CONNECTED))
    assert list(result) == ["frontier", "odo", "perlmutter"]
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
            "resource_uris": [f"{FRONTIER}/api/v1/status/resources/{FRONTIER_ID}"],
        }
    ]
    result = await statuses(make_service(fac), user(**CONNECTED))
    assert result["frontier"].state == "ready"


@pytest.mark.anyio
async def test_open_incident_is_degraded():
    fac = healthy()
    fac.incidents[FRONTIER] = [
        {
            "name": "Lustre maintenance",
            "start": iso(NOW - timedelta(hours=1)),
            "end": iso(NOW + timedelta(hours=3)),
            "resource_uris": [f"{FRONTIER}/api/v1/status/resources/{FRONTIER_ID}"],
        }
    ]
    frontier = (await statuses(make_service(fac), user(**CONNECTED)))["frontier"]
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
            "resource_uris": [f"{FRONTIER}/api/v1/status/resources/{FRONTIER_ID}"],
        }
    ]
    assert (await statuses(make_service(fac), user(**CONNECTED)))[
        "frontier"
    ].state == "ready"


@pytest.mark.anyio
async def test_resource_down_outranks_a_rejected_token():
    fac = healthy()
    fac.status["odo"] = "down"
    fac.iri[(ODO, "odo-tok")] = 401
    odo = (await statuses(make_service(fac), user(**CONNECTED)))["odo"]
    assert odo.state == "degraded"
    assert "down" in odo.checks.facility.message
    assert odo.checks.credential.reason == "rejected"  # still reported in the details


@pytest.mark.anyio
async def test_unreachable_facility_is_never_ready():
    fac = healthy()
    fac.down.add(NERSC)
    pm = (await statuses(make_service(fac), user(**CONNECTED)))["perlmutter"]
    assert pm.checks.facility.reason == "unreachable"
    assert pm.state == "unverifiable"


@pytest.mark.anyio
async def test_other_odo_resources_do_not_stand_in_for_odo():
    """Defiant is down on the same feed; only Odo's pinned id counts."""
    fac = healthy()
    assert (await statuses(make_service(fac), user(**CONNECTED)))[
        "odo"
    ].state == "ready"


@pytest.mark.anyio
async def test_facility_feed_is_shared_across_users():
    fac = healthy()
    service = make_service(fac)
    await service.status(user(**CONNECTED))
    before = sum("/status/" in str(r.url) for r in fac.calls)
    await service.status(user(**CONNECTED))
    assert sum("/status/" in str(r.url) for r in fac.calls) == before


# ---------------------------------------------------------------------------
# Credential check
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_token_means_not_connected_and_no_authenticated_call():
    fac = healthy()
    result = await statuses(make_service(fac), user())
    assert {s.state for s in result.values()} == {"not_connected"}
    assert not any("authorization" in r.headers for r in fac.calls)
    # The facility is still checked, so the details can say it is up.
    assert result["odo"].checks.facility.ok


@pytest.mark.anyio
async def test_rejected_token():
    fac = healthy()
    fac.iri[(NERSC, "pm-tok")] = 401
    fac.s3m["odo-tok"] = (401, None, {})
    result = await statuses(make_service(fac), user(**CONNECTED))
    assert result["perlmutter"].state == "rejected"
    assert result["odo"].state == "rejected"


@pytest.mark.anyio
async def test_token_for_another_project():
    """An Odo token pasted into Frontier: introspect names the wrong project."""
    fac = healthy()
    fac.s3m["fr-tok"] = (200, S.odo_account, {})
    fac.iri[(FRONTIER, "fr-tok")] = 401  # what Frontier's IRI really answers
    frontier = (await statuses(make_service(fac), user(**CONNECTED)))["frontier"]
    assert frontier.state == "wrong_project"
    assert frontier.checks.credential.expected_project == S.frontier_account


@pytest.mark.anyio
async def test_token_not_active_yet():
    fac = healthy()
    start = NOW + timedelta(hours=2)
    fac.s3m["odo-tok"] = (
        200,
        S.odo_account,
        {"delayedStart": True, "delayDate": iso(start)},
    )
    odo = (await statuses(make_service(fac), user(**CONNECTED)))["odo"]
    assert odo.state == "rejected"
    assert odo.checks.credential.reason == "not_active"
    assert odo.checks.credential.active_from == start


@pytest.mark.anyio
async def test_delayed_start_in_the_past_is_fine():
    fac = healthy()
    fac.s3m["odo-tok"] = (
        200,
        S.odo_account,
        {"delayedStart": True, "delayDate": iso(NOW - timedelta(hours=2))},
    )
    assert (await statuses(make_service(fac), user(**CONNECTED)))[
        "odo"
    ].state == "ready"


@pytest.mark.anyio
@pytest.mark.parametrize("status", [403, 500, 502])
async def test_unexpected_iri_answer_is_unverifiable(status):
    fac = healthy()
    fac.iri[(NERSC, "pm-tok")] = status
    pm = (await statuses(make_service(fac), user(**CONNECTED)))["perlmutter"]
    assert pm.state == "unverifiable"
    assert pm.checks.credential.http_status == status


# ---------------------------------------------------------------------------
# Globus check
# ---------------------------------------------------------------------------


def test_globus_source_order():
    deployment = HpcClusterSettings.model_validate(
        {
            "odo_globus_refresh_token": SecretStr("dep-gt"),
            "odo_globus_https_refresh_token": SecretStr("dep-gh"),
        }
    )
    own = user(
        odo_globus_token="o-gt",
        odo_globus_https_token="o-gh",
        globus_token="s-gt",
        globus_https_token="s-gh",
    )
    assert globus_source("odo", own, deployment).transfer == "o-gt"
    shared = user(globus_token="s-gt", globus_https_token="s-gh")
    assert globus_source("odo", shared, deployment).identity == "own"
    assert globus_source("odo", shared, deployment).transfer == "s-gt"
    # Half a pair does not count; the deployment's whole pair takes over.
    half = user(odo_globus_token="o-gt")
    picked = globus_source("odo", half, deployment)
    assert (picked.transfer, picked.identity) == ("dep-gt", "deployment")
    assert globus_source("odo", half, S) is None
    assert globus_source("frontier", half, deployment) is None


@pytest.mark.anyio
async def test_deployment_globus_counts_as_ready():
    fac, globus = healthy(), FakeGlobus()
    deployment = HpcClusterSettings.model_validate(
        {
            "odo_globus_refresh_token": SecretStr("dep-gt"),
            "odo_globus_https_refresh_token": SecretStr("dep-gh"),
        }
    )
    u = user(odo_s3m_token="odo-tok")
    odo = (await statuses(make_service(fac, globus, settings=deployment), u))["odo"]
    assert odo.state == "ready"
    assert odo.checks.globus.identity == "deployment"
    assert globus.calls[0]["collection_id"] == S.odo_globus_collection_id


@pytest.mark.anyio
async def test_globus_not_connected():
    odo = (await statuses(make_service(healthy()), user(odo_s3m_token="odo-tok")))[
        "odo"
    ]
    assert odo.state == "globus_not_connected"


@pytest.mark.anyio
async def test_globus_session_expired():
    globus = FakeGlobus()
    globus.result = GlobusSessionExpired("401")
    odo = (await statuses(make_service(healthy(), globus), user(**CONNECTED)))["odo"]
    assert odo.state == "globus_session_expired"


@pytest.mark.anyio
async def test_globus_failure_of_another_kind_is_unverifiable():
    globus = FakeGlobus()
    globus.result = RuntimeError("network")
    odo = (await statuses(make_service(healthy(), globus), user(**CONNECTED)))["odo"]
    assert odo.state == "unverifiable"


def _globus_error(cls, status: int, code: str, url: str):
    response = requests.Response()
    response.status_code = status
    response._content = b'{"code":"%s","message":"m"}' % code.encode()
    response.headers["Content-Type"] = "application/json"
    response.request = requests.Request("GET", url).prepare()
    return cls(response)


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


def _probe():
    hs.probe_globus(transfer="t", https="h", collection_id="c", client_id="id")


def test_probe_refreshes_both_tokens_then_lists(monkeypatch):
    calls = _patch_globus_sdk(monkeypatch)
    _probe()
    assert calls == [("refresh", "t"), ("refresh", "h"), ("ls", "c", "/~/", 1)]


def test_probe_refresh_refusal_is_session_expired(monkeypatch):
    err = _globus_error(
        GlobusAPIError, 400, "invalid_grant", "https://auth.globus.org/v2/oauth2/token"
    )
    _patch_globus_sdk(monkeypatch, refresh_error=err)
    with pytest.raises(GlobusSessionExpired):
        _probe()


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


# ---------------------------------------------------------------------------
# The endpoint's contract: caching, visibility, secrecy
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_hidden_clusters_make_no_calls():
    fac = healthy()
    u = user(**CONNECTED, hpc_hidden_clusters=["perlmutter"])
    result = await statuses(make_service(fac), u)
    assert "perlmutter" not in result
    assert fac.calls_to(NERSC) == 0


@pytest.mark.anyio
async def test_recent_result_is_reused_and_fresh_reruns():
    fac, clock = healthy(), Clock()
    service = make_service(fac, clock=clock)
    u = user(**CONNECTED)
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
    u = user(**CONNECTED)
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
    u = user(**CONNECTED)
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
    globus.result = GlobusSessionExpired("token odo-gt refused")
    deployment = HpcClusterSettings.model_validate(
        {
            "frontier_globus_refresh_token": SecretStr("dep-secret-gt"),
            "frontier_globus_https_refresh_token": SecretStr("dep-secret-gh"),
        }
    )
    u = user(**{**CONNECTED, "frontier_globus_token": None})
    body = (
        await make_service(fac, globus, settings=deployment).status(u)
    ).model_dump_json()
    for secret in [*CONNECTED.values(), "dep-secret-gt", "dep-secret-gh", "Bearer"]:
        assert secret not in body, secret


@pytest.mark.anyio
async def test_route_passes_fresh_and_cluster_through(monkeypatch):
    from vista_backend.api import users as users_api

    fac = healthy()
    service = make_service(fac)
    monkeypatch.setattr(hs, "hpc_status_service", service)
    u = user(**CONNECTED)
    first = await users_api.get_hpc_status(u)
    assert [c.cluster for c in first.clusters] == ["frontier", "odo", "perlmutter"]
    before = fac.calls_to(NERSC)
    await users_api.get_hpc_status(u, fresh=True, cluster="odo")
    assert fac.calls_to(NERSC) == before
