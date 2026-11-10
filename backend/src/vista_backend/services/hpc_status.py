"""Whether each HPC cluster would work for this researcher right now.

Behind the NavRail's HPC cards. Every card is a live answer, not an inference
from which credentials happen to be saved: the facility is asked whether the
cluster is up, the facility is asked whether it accepts the researcher's
token, and for the OLCF clusters (Odo, Frontier, Lux) the cluster's Globus
collection is asked whether the researcher's session still reaches it.

The calls are the ones a 2026-09-25 spike found could tell a good credential
from a bad one (see the hpc-cards change's design.md):

- facility: the public IRI `status/resources` list plus `status/incidents`, at
  each cluster's configured API version (S3M's v2 for OLCF, NERSC's v1 for
  Perlmutter), the cluster's row matched by its configured resource id -- the
  one jobs are submitted to. The per-resource endpoint is not used -- OLCF's
  v1 reported `unknown` while the list said `up`.
- credential: `GET compute/resources` with the token, at the same version,
  which answers 200 for a valid token and 401 otherwise. The `account/*` endpoints refuse valid S3M
  tokens too, so they cannot tell the two apart. OLCF tokens are also
  introspected, for their project and expiry.
- Globus: exchange both refresh tokens, then list the collection home. A lapsed
  High Assurance session survives the exchange and fails only the listing.

Lux is checked like Frontier, with its own S3M token and Frontier's Globus
connection and collection: Lux mounts Frontier's filesystem.

The checks run here rather than as an MCP tool because the rail is global --
it shows with no project open, and MCP tools are reachable only through a
project's agent -- and because the agent has no reason to see a UI check.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Protocol

import globus_sdk
import httpx
from globus_sdk import gare
from pydantic import BaseModel

from ..config import HpcClusterSettings, hpc_settings
from ..db.schemas import HpcCluster

log = logging.getLogger(__name__)

CLUSTERS: tuple[HpcCluster, ...] = ("frontier", "odo", "perlmutter", "lux")
""" In the order the rail shows them. """

HTTP_TIMEOUT = 5.0
""" Per authenticated call. A facility that has not answered by then is down as far as a researcher is concerned. """
FACILITY_TIMEOUT = 10.0
"""
Per public status call. NERSC's incident feed takes ~1.6 s on its own and
several times that while the other clusters' checks run beside it.
"""
GLOBUS_TIMEOUT = 10.0
""" Two token exchanges and a listing, each its own round trip. """
RESULT_TTL = 60.0
""" How long one researcher's result for one cluster is reused. """
FACILITY_TTL = 60.0
""" How long a facility's public status feed is reused, across researchers. """
INTROSPECT_TTL = 600.0
""" A token's project does not change; matches the MCP server's introspection cache. """

_TITLES: dict[HpcCluster, str] = {
    "frontier": "Frontier",
    "odo": "Odo",
    "perlmutter": "Perlmutter",
    "lux": "Lux",
}


# ---------------------------------------------------------------------------
# What the endpoint returns
# ---------------------------------------------------------------------------

Reason = Literal[
    "degraded",
    "unreachable",
    "unverifiable",
    "not_connected",
    "rejected",
    "not_active",
    "session_expired",
    "invalid",
]
""" Why a check failed. The UI maps these to copy; `message` is a fallback. """

State = Literal[
    "degraded",
    "unverifiable",
    "not_connected",
    "rejected",
    "globus_not_connected",
    "globus_session_expired",
    "ready",
]
""" One per cluster. "Checking" exists only in the UI, while a request is in flight. """


class Incident(BaseModel):
    name: str
    start: datetime | None = None
    end: datetime | None = None


class Check(BaseModel):
    """One check's outcome. Carries no token or token-derived value."""

    ok: bool
    reason: Reason | None = None
    message: str
    http_status: int | None = None
    incident: Incident | None = None
    project: str | None = None
    """
    The project the cluster's jobs run under: the S3M token's, once learned, or
    the researcher's NERSC account setting. A project name, not a secret.
    """
    expires_at: datetime | None = None
    """ S3M `plannedExpiration`. No other credential's expiry is knowable. """
    active_from: datetime | None = None


class ClusterChecks(BaseModel):
    facility: Check
    credential: Check
    globus: Check | None = None
    """ The OLCF clusters only (Lux through Frontier's connection); Perlmutter moves files through IRI. """
    settings: Check
    """
    The researcher's own settings a submission needs: the remote directory,
    and the NERSC account on Perlmutter. Read from the settings row, so it is
    never cached.
    """


class ClusterStatus(BaseModel):
    cluster: HpcCluster
    state: State
    checked_at: datetime
    checks: ClusterChecks


class HpcStatus(BaseModel):
    clusters: list[ClusterStatus]


# ---------------------------------------------------------------------------
# One state per cluster
# ---------------------------------------------------------------------------


def _checks(c: ClusterChecks) -> list[Check]:
    return [c.facility, c.credential, c.settings] + ([c.globus] if c.globus else [])


_PRECEDENCE: list[tuple[State, Callable[[ClusterChecks], bool]]] = [
    ("degraded", lambda c: c.facility.reason == "degraded"),
    (
        "unverifiable",
        lambda c: any(x.reason in ("unreachable", "unverifiable") for x in _checks(c)),
    ),
    (
        "not_connected",
        lambda c: (
            c.credential.reason == "not_connected"
            or c.settings.reason in ("not_connected", "invalid")
        ),
    ),
    ("rejected", lambda c: c.credential.reason in ("rejected", "not_active")),
    (
        "globus_not_connected",
        lambda c: c.globus is not None and c.globus.reason == "not_connected",
    ),
    (
        "globus_session_expired",
        lambda c: c.globus is not None and c.globus.reason == "session_expired",
    ),
]
"""
First match wins. A facility that is down explains everything else, so it
comes first; a credential problem comes before a Globus one because without
the credential no job runs at all.
"""


def resolve_state(checks: ClusterChecks) -> State:
    for state, applies in _PRECEDENCE:
        if applies(checks):
            return state
    if all(c.ok for c in _checks(checks)):
        return "ready"
    # A failed check whose reason nothing above claims: say so rather than
    # show green.
    return "unverifiable"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

_SAFE_REMOTE_DIR = re.compile(r"/[A-Za-z0-9._+,:=@/-]*")
""" The MCP server's rule (`UserConfig.require_remote_dir`): the card refuses what submission would. """


def settings_check(cluster: HpcCluster, local: Any) -> Check:
    """Whether the researcher has set what a submission to `cluster` needs.

    Every cluster needs a remote directory, and Perlmutter a NERSC account
    too (the OLCF clusters charge their S3M token's project). A value submission would refuse -- a relative path, `/`, shell
    characters, or a `<project>` placeholder left in -- fails the check as
    well, so the card never shows Ready for a cluster every job would fail on.
    """
    title = _TITLES[cluster]
    remote_dir: str | None = {
        "odo": local.odo_remote_dir,
        "frontier": local.frontier_remote_dir,
        "perlmutter": local.nersc_remote_dir,
        "lux": local.lux_remote_dir,
    }[cluster]
    needs_account = cluster == "perlmutter"
    account: str | None = local.nersc_account if needs_account else None
    account_label = "NERSC account"

    missing = [
        label
        for label, value, needed in (
            (account_label, account, needs_account),
            (f"{title} remote directory", remote_dir, True),
        )
        if needed and not value
    ]
    if missing or remote_dir is None:
        return Check(
            ok=False,
            reason="not_connected",
            message=f"No {' or '.join(missing)} is set.",
        )
    if not _SAFE_REMOTE_DIR.fullmatch(remote_dir) or not remote_dir.rstrip("/"):
        return Check(
            ok=False,
            reason="invalid",
            message=(
                f"The {title} remote directory {remote_dir} can't be used. It must be an "
                "absolute path other than /, using only letters, digits and "
                ". _ + , : = @ - /. Replace any <project> with the project's name."
            ),
        )
    return Check(
        ok=True,
        message=remote_dir.rstrip("/"),
        project=account if needs_account else None,
    )


# ---------------------------------------------------------------------------
# Globus
# ---------------------------------------------------------------------------


class GlobusSessionExpired(Exception):
    """The researcher has to connect Globus again for this cluster."""

    def __init__(
        self, detail: str, log: str = "", http_status: int | None = None
    ) -> None:
        super().__init__(detail)
        self.detail, self.log, self.http_status = detail, log, http_status


class GlobusCheckFailed(Exception):
    """Globus answered, but not in a way that says whether the session works;
    the card reads Couldn't verify."""

    def __init__(
        self, detail: str, log: str = "", http_status: int | None = None
    ) -> None:
        super().__init__(detail)
        self.detail, self.log, self.http_status = detail, log, http_status


def _describe(error: globus_sdk.GlobusAPIError) -> tuple[str, str]:
    """(detail, log) for a Globus answer: status, code and message, no headers."""
    code, message = error.code, error.message
    detail = f"Globus answered HTTP {error.http_status}"
    if code:
        detail += f" ({code})"
    if message:
        detail += f": {message}"
    log = f"HTTP {error.http_status} code={code} body={(error.text or '')[:500]}"
    return detail + ".", log


class GlobusProbe(Protocol):
    def __call__(
        self,
        *,
        transfer: str,
        https: str,
        collection_id: str,
        client_id: str,
        title: str,
    ) -> None: ...


def probe_globus(
    *,
    transfer: str,
    https: str,
    collection_id: str,
    client_id: str,
    title: str = "this cluster",
) -> None:
    """Prove a Globus pair still reaches the collection. Blocking.

    Classifies failures the way the MCP server's `lib/globus.py` does, so the
    card and the file operations agree on what an expired session is. A GARE
    (Globus's re-authentication answer) is checked first, whatever its status,
    because an expired High Assurance login answers 502. Then: a refresh token
    Globus will not exchange, a Transfer 401, or a `ConsentRequired` /
    `AuthenticationFailed` refusal. Raises GlobusSessionExpired or
    GlobusCheckFailed, and lets network errors through.
    """
    auth_client = globus_sdk.NativeAppAuthClient(client_id)
    try:
        # Each authorizer exchanges its refresh token on construction, so
        # building both proves both halves of the pair.
        transfer_auth = globus_sdk.RefreshTokenAuthorizer(transfer, auth_client)
        globus_sdk.RefreshTokenAuthorizer(https, auth_client)
    except globus_sdk.GlobusAPIError as error:
        detail, log = _describe(error)
        raw = error.raw_json
        if (
            error.http_status == 400
            and isinstance(raw, dict)
            and raw.get("error") == "invalid_grant"
        ):
            raise GlobusSessionExpired(
                f"Globus no longer accepts the saved {title} connection. "
                "Connect Globus again.",
                log=log,
                http_status=400,
            ) from error
        raise GlobusCheckFailed(
            f"Globus token exchange: {detail}", log, error.http_status
        ) from error

    client = globus_sdk.TransferClient(authorizer=transfer_auth)
    try:
        client.operation_ls(collection_id, path="/~/", limit=1)
    except globus_sdk.TransferAPIError as error:
        detail, log = _describe(error)
        reauth = gare.to_gare(error)
        if reauth is not None:
            params = reauth.authorization_parameters
            required = params.session_required_single_domain or []
            domains = [d for d in required if d != "clients.auth.globus.org"]
            if domains:
                detail = (
                    f"The Globus session for {title} has expired: Globus needs a "
                    f"fresh login with {', '.join(domains)}. Connect Globus again."
                )
            else:
                detail = (
                    f"The Globus session for {title} has expired. Connect Globus again."
                )
            raise GlobusSessionExpired(
                detail, log=log, http_status=error.http_status
            ) from error
        code = error.code or ""
        if error.http_status == 401 or code in (
            "ConsentRequired",
            "AuthenticationFailed",
        ):
            raise GlobusSessionExpired(
                f"The Globus session for {title} has expired. Connect Globus again.",
                log=log,
                http_status=error.http_status,
            ) from error
        if error.http_status in (403, 404):
            # Authenticated, and the collection answered; the home directory
            # just is not listable. The session is what this check is about.
            return
        raise GlobusCheckFailed(detail, log, error.http_status) from error


@dataclass(frozen=True)
class _GlobusSource:
    transfer: str
    https: str


OlcfCluster = Literal["odo", "frontier", "lux"]


def globus_owner(cluster: OlcfCluster) -> Literal["odo", "frontier"]:
    """Whose Globus connection and collection `cluster` uses: Frontier's for Lux, which mounts it."""
    return "frontier" if cluster == "lux" else cluster


def globus_source(cluster: OlcfCluster, local: Any) -> _GlobusSource | None:
    """The Globus pair this cluster's file operations would use, or None.

    The same order as the MCP server's `UserConfig.require_globus_token`: the
    researcher's pair for this cluster (Frontier's, for Lux), then their shared
    pair. There is no deployment-wide pair. A source counts only with both
    halves -- half a pair lists a directory it cannot read.
    """
    cluster = globus_owner(cluster)
    own = (
        (local.odo_globus_token, local.odo_globus_https_token)
        if cluster == "odo"
        else (local.frontier_globus_token, local.frontier_globus_https_token)
    )
    for transfer, https in (own, (local.globus_token, local.globus_https_token)):
        if transfer and https:
            return _GlobusSource(transfer, https)
    return None


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _digest(*values: str | None) -> str:
    return hashlib.sha256("\0".join(v or "" for v in values).encode()).hexdigest()


class HpcStatusService:
    """Runs the checks, with the caches that keep them cheap.

    Everything that reaches the network is injectable -- the HTTP transport,
    the Globus probe, and the clock -- so the tests run with none.
    """

    def __init__(
        self,
        *,
        settings: HpcClusterSettings = hpc_settings,
        transport: httpx.AsyncBaseTransport | None = None,
        globus_probe: GlobusProbe = probe_globus,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._globus_probe = globus_probe
        self._now = now
        self._monotonic = monotonic
        self._results: dict[HpcCluster, tuple[float, str, ClusterStatus]] = {}
        self._facility_feeds: dict[str, tuple[float, list[dict], list[dict]]] = {}
        self._introspections: dict[str, tuple[float, dict]] = {}

    async def status(
        self, local: Any, *, fresh: bool = False, cluster: HpcCluster | None = None
    ) -> HpcStatus:
        """Every visible cluster's status for these settings.

        `fresh` reruns the checks instead of reusing a recent result: for every
        cluster, or with `cluster` for that one only. A hidden cluster is not
        checked at all.
        """
        hidden: set[str] = set(local.hpc_hidden_clusters or [])
        visible: list[HpcCluster] = [c for c in CLUSTERS if c not in hidden]
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT, transport=self._transport
        ) as client:
            results = await asyncio.gather(
                *(
                    self._cluster(
                        client, local, c, fresh=fresh and cluster in (None, c)
                    )
                    for c in visible
                )
            )
        return HpcStatus(clusters=list(results))

    async def _cluster(
        self, client: httpx.AsyncClient, local: Any, cluster: HpcCluster, *, fresh: bool
    ) -> ClusterStatus:
        key = cluster
        fingerprint = self._credential_fingerprint(local, cluster)
        settings = settings_check(cluster, local)
        cached = self._results.get(key)
        if (
            not fresh
            and cached is not None
            and cached[1] == fingerprint
            and self._monotonic() - cached[0] < RESULT_TTL
        ):
            # The settings come from the settings row, not a facility, so a cached
            # result always carries the current ones.
            checks = cached[2].checks.model_copy(update={"settings": settings})
            return cached[2].model_copy(
                update={"checks": checks, "state": resolve_state(checks)}
            )

        olcf = cluster != "perlmutter"
        facility, credential, globus = await asyncio.gather(
            self._facility(client, cluster, fresh=fresh),
            self._credential(client, cluster, local),
            self._globus(cluster, local) if olcf else _none(),
        )
        checks = ClusterChecks(
            facility=facility, credential=credential, globus=globus, settings=settings
        )
        result = ClusterStatus(
            cluster=cluster,
            state=resolve_state(checks),
            checked_at=self._now(),
            checks=checks,
        )
        self._results[key] = (self._monotonic(), fingerprint, result)
        return result

    def _credential_fingerprint(self, local: Any, cluster: HpcCluster) -> str:
        """Changes whenever a credential this cluster's checks use changes."""
        if cluster == "perlmutter":
            return _digest(local.nersc_iri_token)
        owner = globus_owner(cluster)
        return _digest(
            _s3m_token(local, cluster),
            getattr(local, f"{owner}_globus_token"),
            getattr(local, f"{owner}_globus_https_token"),
            local.globus_token,
            local.globus_https_token,
        )

    # --- facility ----------------------------------------------------------

    def _iri_api(self, cluster: HpcCluster) -> str:
        """The cluster's IRI API root at its configured version, e.g. `.../api/v2`."""
        base = getattr(self._settings, f"{cluster}_iri_url").rstrip("/")
        return f"{base}/api/{getattr(self._settings, f'{cluster}_iri_api_version')}"

    def _resource_id(self, cluster: HpcCluster) -> str:
        return getattr(self._settings, f"{cluster}_iri_resource_id")

    async def _facility_feed(
        self, client: httpx.AsyncClient, api: str, *, fresh: bool
    ) -> tuple[list[dict], list[dict]]:
        cached = self._facility_feeds.get(api)
        if not fresh and cached and self._monotonic() - cached[0] < FACILITY_TTL:
            return cached[1], cached[2]
        # Only incidents active now. Unfiltered, NERSC returns its *oldest*
        # hundred, which never include a current one; OLCF answers "none
        # active" with a 404 rather than an empty list.
        resources, incidents = await asyncio.gather(
            client.get(f"{api}/status/resources", timeout=FACILITY_TIMEOUT),
            client.get(
                f"{api}/status/incidents",
                params={"time": self._now().strftime("%Y-%m-%dT%H:%M:%SZ")},
                timeout=FACILITY_TIMEOUT,
            ),
        )
        resources.raise_for_status()
        rows: Any = resources.json()
        active: Any = []
        if incidents.status_code != 404:
            incidents.raise_for_status()
            active = incidents.json()
        if not (isinstance(rows, list) and isinstance(active, list)):
            raise ValueError("status feed is not a list")
        self._facility_feeds[api] = (self._monotonic(), rows, active)
        return rows, active

    def _match_resource(self, cluster: HpcCluster, rows: list[dict]) -> dict | None:
        """
        The cluster's row, by the resource id jobs are submitted to -- never by
        name or position: the moderate enclave lists Lux beside Frontier.
        """
        want = self._resource_id(cluster)
        for row in rows:
            if isinstance(row, dict) and row.get("id") == want:
                return row
        return None

    def _open_incident(
        self, resource_id: str, incidents: list[dict]
    ) -> Incident | None:
        now = self._now()
        for raw in incidents:
            if not isinstance(raw, dict):
                continue
            uris = raw.get("resource_uris") or []
            if not any(str(u).rstrip("/").endswith(f"/{resource_id}") for u in uris):
                continue
            start, end = _parse_time(raw.get("start")), _parse_time(raw.get("end"))
            if (start is None or start <= now) and (end is None or end > now):
                return Incident(
                    name=str(raw.get("name") or "Incident"), start=start, end=end
                )
        return None

    async def _facility(
        self, client: httpx.AsyncClient, cluster: HpcCluster, *, fresh: bool
    ) -> Check:
        title = _TITLES[cluster]
        try:
            resources, incidents = await self._facility_feed(
                client, self._iri_api(cluster), fresh=fresh
            )
        except (httpx.HTTPError, ValueError) as error:
            log.info("%s status feed unavailable: %s", title, type(error).__name__)
            return Check(
                ok=False,
                reason="unreachable",
                message=f"{title}'s facility status feed did not answer.",
            )
        row = self._match_resource(cluster, resources)
        if row is None:
            return Check(
                ok=False,
                reason="unverifiable",
                message=f"The facility status feed does not list {title}.",
            )
        status = str(row.get("current_status") or "unknown")
        incident = self._open_incident(str(row.get("id", "")), incidents)
        if status != "up":
            return Check(
                ok=False,
                reason="degraded",
                message=f"The facility reports {title} as {status}.",
                incident=incident,
            )
        if incident is not None:
            return Check(
                ok=False,
                reason="degraded",
                message=f"{title} has an open incident: {incident.name}.",
                incident=incident,
            )
        return Check(ok=True, message=f"The facility reports {title} up.")

    # --- credential --------------------------------------------------------

    async def _get_status(
        self, client: httpx.AsyncClient, url: str, token: str
    ) -> int | None:
        """The HTTP status an authenticated GET answers with, or None if none came."""
        try:
            response = await client.get(
                url, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError:
            return None
        return response.status_code

    async def _introspect(
        self, client: httpx.AsyncClient, url: str, token: str
    ) -> tuple[int | None, dict | None]:
        key = _digest(url, token)
        cached = self._introspections.get(key)
        if cached and self._monotonic() - cached[0] < INTROSPECT_TTL:
            return 200, cached[1]
        try:
            response = await client.get(
                url, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError:
            return None, None
        if response.status_code != 200:
            return response.status_code, None
        try:
            info = response.json().get("token") or {}
        except ValueError, AttributeError:
            return response.status_code, None
        self._introspections[key] = (self._monotonic(), info)
        return 200, info

    async def _credential(
        self, client: httpx.AsyncClient, cluster: HpcCluster, local: Any
    ) -> Check:
        title = _TITLES[cluster]
        kind = "NERSC IRI" if cluster == "perlmutter" else "S3M"
        token = (
            local.nersc_iri_token
            if cluster == "perlmutter"
            else _s3m_token(local, cluster)
        )
        if not token:
            return Check(
                ok=False,
                reason="not_connected",
                message=f"No {kind} token is saved for {title}.",
            )
        compute_url = f"{self._iri_api(cluster)}/compute/resources"

        if cluster == "perlmutter":
            return self._from_iri(
                title, kind, await self._get_status(client, compute_url, token)
            )

        # Lux's tokens are S3M's like Frontier's, and introspected at the same place.
        introspect_url = (
            self._settings.odo_introspect_url
            if cluster == "odo"
            else self._settings.frontier_introspect_url
        )
        (intro_status, info), iri_status = await asyncio.gather(
            self._introspect(client, introspect_url, token),
            self._get_status(client, compute_url, token),
        )
        if intro_status == 401:
            return Check(
                ok=False,
                reason="rejected",
                message=f"S3M rejected the {title} token; it may have expired or been revoked.",
                http_status=401,
            )
        if info is None:
            return Check(
                ok=False,
                reason="unverifiable",
                message="S3M could not be asked about the token.",
                http_status=intro_status,
            )

        project = info.get("project") or None
        expires_at = _parse_time(info.get("plannedExpiration"))
        active_from = (
            _parse_time(info.get("delayDate")) if info.get("delayedStart") else None
        )
        if active_from is not None and active_from > self._now():
            return Check(
                ok=False,
                reason="not_active",
                message=f"The {title} token is not active until {active_from.isoformat()}.",
                project=project,
                active_from=active_from,
                expires_at=expires_at,
            )
        # Any project is accepted: it is the account the cluster's jobs are
        # charged to, reported rather than compared with anything.
        check = self._from_iri(title, kind, iri_status)
        return check.model_copy(update={"project": project, "expires_at": expires_at})

    @staticmethod
    def _from_iri(title: str, kind: str, status: int | None) -> Check:
        if status == 200:
            return Check(ok=True, message=f"{title} accepted the {kind} token.")
        if status == 401:
            return Check(
                ok=False,
                reason="rejected",
                message=f"{title} rejected the {kind} token; it may have expired.",
                http_status=401,
            )
        return Check(
            ok=False,
            reason="unverifiable",
            message=(
                f"{title} gave an unexpected answer ({status}) when checking the token."
                if status is not None
                else f"{title} did not answer when checking the token."
            ),
            http_status=status,
        )

    # --- Globus ------------------------------------------------------------

    async def _globus(self, cluster: OlcfCluster, local: Any) -> Check:
        title = _TITLES[cluster]
        shared = " (Lux uses Frontier's connection)" if cluster == "lux" else ""
        source = globus_source(cluster, local)
        if source is None:
            return Check(
                ok=False,
                reason="not_connected",
                message=f"Globus file transfer is not connected for {title}{shared}.",
            )
        collection = getattr(
            self._settings, f"{globus_owner(cluster)}_globus_collection_id"
        )
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    self._globus_probe,
                    transfer=source.transfer,
                    https=source.https,
                    collection_id=collection,
                    client_id=self._settings.globus_native_app_client_id,
                    title=title,
                ),
                GLOBUS_TIMEOUT,
            )
        except GlobusSessionExpired as error:
            log.info("%s Globus check failed: %s", title, error.log or error.detail)
            return Check(
                ok=False,
                reason="session_expired",
                message=error.detail,
                http_status=error.http_status,
            )
        except GlobusCheckFailed as error:
            log.info("%s Globus check failed: %s", title, error.log or error.detail)
            return Check(
                ok=False,
                reason="unverifiable",
                message=error.detail,
                http_status=error.http_status,
            )
        except TimeoutError:
            log.info(
                "%s Globus check failed: no answer within %g s", title, GLOBUS_TIMEOUT
            )
            return Check(
                ok=False,
                reason="unverifiable",
                message=f"Globus did not answer within {GLOBUS_TIMEOUT:g} s",
            )
        except globus_sdk.NetworkError as error:
            log.info("%s Globus check failed: %s", title, type(error).__name__)
            return Check(
                ok=False,
                reason="unverifiable",
                message="Globus could not be reached",
            )
        except Exception as error:  # noqa: BLE001 -- any other failure is "couldn't tell"
            log.info("%s Globus check failed: %s", title, type(error).__name__)
            return Check(
                ok=False,
                reason="unverifiable",
                message=f"Globus did not confirm {title}'s file transfer.",
            )
        return Check(ok=True, message=f"Globus reaches {title}'s files.")


async def _none() -> None:
    return None


def _s3m_token(local: Any, cluster: HpcCluster) -> str | None:
    return getattr(local, f"{cluster}_s3m_token")


hpc_status_service = HpcStatusService()
