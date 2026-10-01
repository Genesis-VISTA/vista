"""Whether each HPC cluster would work for this researcher right now.

Behind the NavRail's HPC cards. Every card is a live answer, not an inference
from which credentials happen to be saved: the facility is asked whether the
cluster is up, the facility is asked whether it accepts the researcher's
token, and for Odo and Frontier the cluster's Globus collection is asked
whether the researcher's session still reaches it.

The calls are the ones a 2026-09-25 spike found could tell a good credential
from a bad one (see the hpc-cards change's design.md):

- facility: the public IRI `status/resources` list plus `status/incidents`.
  The per-resource endpoint is not used -- OLCF's reports `unknown` while the
  list says `up`.
- credential: `GET compute/resources` with the token, which answers 200 for a
  valid token and 401 otherwise. The `account/*` endpoints refuse valid S3M
  tokens too, so they cannot tell the two apart. OLCF tokens are also
  introspected, for their project and expiry.
- Globus: exchange both refresh tokens, then list the collection home. A lapsed
  High Assurance session survives the exchange and fails only the listing.

Lux is the exception (see the lux-hpc-card change): it has no IRI service and
no stored credential -- a researcher signs in with a PIN and RSA passcode when
a chat first uses it. Its facility check is whether the hub's SSH server
answers, and its credential check always passes, saying how sign-in works.

The checks run here rather than as an MCP tool because the rail is global --
it shows with no project open, and MCP tools are reachable only through a
project's agent -- and because the agent has no reason to see a UI check.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Protocol

import globus_sdk
import httpx
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
LUX_PROBE_MIN_INTERVAL = 10.0
"""
The Lux hub is probed at most this often, even for a fresh check: every probe
is a pre-login SSH connection from the VISTA host, the same address the MCP
server submits Lux jobs from, and sshd penalises bursts of those.
"""
LUX_PROBE_FAILURE_TTL = 10.0
""" How long a failed hub probe is reused. Shorter than a success, so one dropped connection doesn't keep Lux grey for a minute. """
INTROSPECT_TTL = 600.0
""" A token's project does not change; matches the MCP server's introspection cache. """

_TITLES: dict[HpcCluster, str] = {
    "frontier": "Frontier",
    "odo": "Odo",
    "perlmutter": "Perlmutter",
    "lux": "Lux",
}

SSH_PORT = 22
PROBE_IDENTIFICATION = b"SSH-2.0-VISTA_status_probe\r\n"
"""
Sent back after the hub's greeting, so the hub logs a named client that hung
up before key exchange rather than a silent connection -- the pattern scanners
leave and fail2ban-style filters match.
"""
LUX_SIGN_IN = "Sign in with PIN + RSA passcode when a chat first uses Lux."


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
    The project the cluster's jobs run under: the S3M token's, once learned.
    Lux has none to report -- its jobs go to the researcher's default Slurm
    account. A project name, not a secret.
    """
    expires_at: datetime | None = None
    """ S3M `plannedExpiration`. No other credential's expiry is knowable. """
    active_from: datetime | None = None


class ClusterChecks(BaseModel):
    facility: Check
    credential: Check
    globus: Check | None = None
    """ Odo and Frontier only; Perlmutter and Lux move no files through Globus. """


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
    return [c.facility, c.credential] + ([c.globus] if c.globus else [])


_PRECEDENCE: list[tuple[State, Callable[[ClusterChecks], bool]]] = [
    ("degraded", lambda c: c.facility.reason == "degraded"),
    (
        "unverifiable",
        lambda c: any(x.reason in ("unreachable", "unverifiable") for x in _checks(c)),
    ),
    ("not_connected", lambda c: c.credential.reason == "not_connected"),
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
# Globus
# ---------------------------------------------------------------------------


class GlobusSessionExpired(Exception):
    """The researcher has to connect Globus again for this cluster."""


class GlobusProbe(Protocol):
    def __call__(
        self, *, transfer: str, https: str, collection_id: str, client_id: str
    ) -> None: ...


def probe_globus(
    *, transfer: str, https: str, collection_id: str, client_id: str
) -> None:
    """Prove a Globus pair still reaches the collection. Blocking.

    Classifies failures the way the MCP server's `lib/globus.py` does, so the
    card and the file operations agree on what an expired session is: a
    refresh token Globus will not exchange, a Transfer 401, or a
    `ConsentRequired` / `AuthenticationFailed` refusal.
    """
    auth_client = globus_sdk.NativeAppAuthClient(client_id)
    try:
        # Each authorizer exchanges its refresh token on construction, so
        # building both proves both halves of the pair.
        transfer_auth = globus_sdk.RefreshTokenAuthorizer(transfer, auth_client)
        globus_sdk.RefreshTokenAuthorizer(https, auth_client)
    except globus_sdk.GlobusAPIError as error:
        raise GlobusSessionExpired(error.message) from error

    client = globus_sdk.TransferClient(authorizer=transfer_auth)
    try:
        client.operation_ls(collection_id, path="/~/", limit=1)
    except globus_sdk.TransferAPIError as error:
        code = error.code or ""
        if error.http_status == 401 or code in (
            "ConsentRequired",
            "AuthenticationFailed",
        ):
            raise GlobusSessionExpired(error.message) from error
        if error.http_status in (403, 404):
            # Authenticated, and the collection answered; the home directory
            # just is not listable. The session is what this check is about.
            return
        raise


@dataclass(frozen=True)
class _GlobusSource:
    transfer: str
    https: str


def globus_source(
    cluster: Literal["odo", "frontier"], user: Any
) -> _GlobusSource | None:
    """The Globus pair this cluster's file operations would use, or None.

    The same order as the MCP server's `UserConfig.require_globus_token`: the
    researcher's pair for this cluster, then their shared pair. There is no
    deployment-wide pair. A source counts only with both halves -- half a pair
    lists a directory it cannot read.
    """
    own = (
        (user.odo_globus_token, user.odo_globus_https_token)
        if cluster == "odo"
        else (user.frontier_globus_token, user.frontier_globus_https_token)
    )
    for transfer, https in (own, (user.globus_token, user.globus_https_token)):
        if transfer and https:
            return _GlobusSource(transfer, https)
    return None


# ---------------------------------------------------------------------------
# Lux
# ---------------------------------------------------------------------------


class SshProbeError(Exception):
    """The host answered, but not as an SSH server."""


class LuxProbe(Protocol):
    async def __call__(self, host: str, *, timeout: float) -> str: ...


async def probe_ssh_greeting(host: str, *, timeout: float, port: int = SSH_PORT) -> str:
    """The SSH greeting `host` sends, e.g. `SSH-2.0-OpenSSH_8.7`.

    Reads the server's first line, answers with `PROBE_IDENTIFICATION`, and
    hangs up: no key exchange, no authentication. Raises `TimeoutError`,
    `OSError` (DNS, refused, unreachable), or `SshProbeError`.
    """

    async def talk() -> str:
        # RFC 4253 caps the identification line at 255 bytes; a longer first
        # line overruns the reader's limit and is not an SSH server.
        reader, writer = await asyncio.open_connection(host, port, limit=256)
        try:
            try:
                line = await reader.readline()
            except ValueError as error:
                raise SshProbeError(
                    "sent a first line too long for an SSH greeting"
                ) from error
            if not line:
                raise SshProbeError("closed the connection without a greeting")
            if not line.startswith(b"SSH-"):
                raise SshProbeError("answered, but not with an SSH greeting")
            writer.write(PROBE_IDENTIFICATION)
            await writer.drain()
            return line.decode("ascii", "replace").strip()
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    return await asyncio.wait_for(talk(), timeout)


def _probe_failure(error: BaseException, timeout: float) -> str:
    if isinstance(error, TimeoutError):
        return f"did not answer within {timeout:g} s"
    if isinstance(error, socket.gaierror):
        return "could not be resolved"
    if isinstance(error, ConnectionRefusedError):
        return "refused the connection"
    if isinstance(error, SshProbeError):
        return str(error)
    if isinstance(error, OSError):
        return f"could not be reached ({error.strerror or type(error).__name__})"
    return f"could not be checked ({type(error).__name__})"


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
    the Globus and Lux probes, and the clock -- so the tests run with none.
    """

    def __init__(
        self,
        *,
        settings: HpcClusterSettings = hpc_settings,
        transport: httpx.AsyncBaseTransport | None = None,
        globus_probe: GlobusProbe = probe_globus,
        lux_probe: LuxProbe = probe_ssh_greeting,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._globus_probe = globus_probe
        self._lux_probe = lux_probe
        self._now = now
        self._monotonic = monotonic
        self._results: dict[
            tuple[str, HpcCluster], tuple[float, str, ClusterStatus]
        ] = {}
        self._facility_feeds: dict[str, tuple[float, list[dict], list[dict]]] = {}
        self._introspections: dict[str, tuple[float, dict]] = {}
        self._ssh_probes: dict[str, tuple[float, Check]] = {}
        self._ssh_inflight: dict[str, asyncio.Future[Check]] = {}

    async def status(
        self, user: Any, *, fresh: bool = False, cluster: HpcCluster | None = None
    ) -> HpcStatus:
        """Every visible cluster's status for `user`.

        `fresh` reruns the checks instead of reusing a recent result: for every
        cluster, or with `cluster` for that one only. A hidden cluster is not
        checked at all.
        """
        hidden: set[str] = set(user.hpc_hidden_clusters or [])
        visible: list[HpcCluster] = [c for c in CLUSTERS if c not in hidden]
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT, transport=self._transport
        ) as client:
            results = await asyncio.gather(
                *(
                    self._cluster(client, user, c, fresh=fresh and cluster in (None, c))
                    for c in visible
                )
            )
        return HpcStatus(clusters=list(results))

    async def _cluster(
        self, client: httpx.AsyncClient, user: Any, cluster: HpcCluster, *, fresh: bool
    ) -> ClusterStatus:
        key = (str(user.id), cluster)
        fingerprint = self._credential_fingerprint(user, cluster)
        cached = self._results.get(key)
        if (
            not fresh
            and cached is not None
            and cached[1] == fingerprint
            and self._monotonic() - cached[0] < self._result_ttl(cached[2])
        ):
            return cached[2]

        olcf = cluster in ("odo", "frontier")
        facility, credential, globus = await asyncio.gather(
            self._facility(client, cluster, fresh=fresh),
            self._credential(client, cluster, user),
            self._globus(cluster, user) if olcf else _none(),
        )
        checks = ClusterChecks(facility=facility, credential=credential, globus=globus)
        result = ClusterStatus(
            cluster=cluster,
            state=resolve_state(checks),
            checked_at=self._now(),
            checks=checks,
        )
        self._results[key] = (self._monotonic(), fingerprint, result)
        return result

    @staticmethod
    def _result_ttl(result: ClusterStatus) -> float:
        """A failed Lux hub probe is kept no longer per researcher than it is shared."""
        if result.cluster == "lux" and not result.checks.facility.ok:
            return LUX_PROBE_FAILURE_TTL
        return RESULT_TTL

    def _credential_fingerprint(self, user: Any, cluster: HpcCluster) -> str:
        """Changes whenever a credential this cluster's checks use changes."""
        if cluster == "lux":
            return _digest()  # no credential feeds Lux's checks
        if cluster == "perlmutter":
            return _digest(user.nersc_iri_token)
        s3m = user.odo_s3m_token if cluster == "odo" else user.frontier_s3m_token
        own = (
            (user.odo_globus_token, user.odo_globus_https_token)
            if cluster == "odo"
            else (user.frontier_globus_token, user.frontier_globus_https_token)
        )
        return _digest(s3m, *own, user.globus_token, user.globus_https_token)

    # --- facility ----------------------------------------------------------

    def _iri_url(self, cluster: HpcCluster) -> str:
        return {
            "odo": self._settings.odo_iri_url,
            "frontier": self._settings.frontier_iri_url,
            "perlmutter": self._settings.nersc_iri_url,
        }[cluster].rstrip("/")

    async def _facility_feed(
        self, client: httpx.AsyncClient, base: str, *, fresh: bool
    ) -> tuple[list[dict], list[dict]]:
        cached = self._facility_feeds.get(base)
        if not fresh and cached and self._monotonic() - cached[0] < FACILITY_TTL:
            return cached[1], cached[2]
        # Only incidents active now. Unfiltered, NERSC returns its *oldest*
        # hundred, which never include a current one; OLCF answers "none
        # active" with a 404 rather than an empty list.
        resources, incidents = await asyncio.gather(
            client.get(f"{base}/api/v1/status/resources", timeout=FACILITY_TIMEOUT),
            client.get(
                f"{base}/api/v1/status/incidents",
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
        self._facility_feeds[base] = (self._monotonic(), rows, active)
        return rows, active

    def _match_resource(self, cluster: HpcCluster, rows: list[dict]) -> dict | None:
        for row in rows:
            if not isinstance(row, dict):
                continue
            if (
                cluster == "odo"
                and row.get("id") == self._settings.odo_compute_resource_id
            ):
                return row
            if (
                cluster == "frontier"
                and str(row.get("name", "")).lower() == self._settings.frontier_machine
            ):
                return row
            if (
                cluster == "perlmutter"
                and row.get("group") == self._settings.nersc_machine
                and row.get("name") == "compute"
            ):
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
        if cluster == "lux":
            return await self._lux_facility(fresh=fresh)
        title = _TITLES[cluster]
        try:
            resources, incidents = await self._facility_feed(
                client, self._iri_url(cluster), fresh=fresh
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

    async def _lux_facility(self, *, fresh: bool) -> Check:
        """Whether the Lux hub's SSH server answers. Shared across researchers.

        Every failure is `unreachable` (Couldn't verify), never `degraded`: a
        probe that fails cannot tell Lux being down from a network that does
        not reach ORNL. Concurrent callers share one probe, and even a fresh
        check reuses a probe younger than `LUX_PROBE_MIN_INTERVAL`.
        """
        hosts = self._settings.lux_ssh_hosts
        if not hosts:
            return Check(
                ok=False,
                reason="unverifiable",
                message="No Lux hub is configured (VISTA_MCP_LUX_SSH_HOSTS is empty).",
            )
        host = hosts[0]
        cached = self._ssh_probes.get(host)
        if cached:
            age = self._monotonic() - cached[0]
            ttl = FACILITY_TTL if cached[1].ok else LUX_PROBE_FAILURE_TTL
            if age < LUX_PROBE_MIN_INTERVAL or (not fresh and age < ttl):
                return cached[1]
        inflight = self._ssh_inflight.get(host)
        if inflight is None:
            inflight = asyncio.ensure_future(self._probe_lux_hub(host))
            self._ssh_inflight[host] = inflight
            inflight.add_done_callback(lambda _: self._ssh_inflight.pop(host, None))
        # Shielded: one caller giving up must not cancel the others' probe.
        return await asyncio.shield(inflight)

    async def _probe_lux_hub(self, host: str) -> Check:
        try:
            greeting = await self._lux_probe(host, timeout=HTTP_TIMEOUT)
        except Exception as error:  # noqa: BLE001 -- any failure is "couldn't tell", never a 500
            log.info("Lux hub %s probe failed: %s", host, type(error).__name__)
            check = Check(
                ok=False,
                reason="unreachable",
                message=f"The Lux hub {host} {_probe_failure(error, HTTP_TIMEOUT)}.",
            )
        else:
            check = Check(
                ok=True,
                message=f"The Lux hub {host} answered ({greeting}).",
            )
        self._ssh_probes[host] = (self._monotonic(), check)
        return check

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
        self, client: httpx.AsyncClient, cluster: HpcCluster, user: Any
    ) -> Check:
        if cluster == "lux":
            # Nothing is stored to check; see LUX_SIGN_IN.
            return Check(ok=True, message=LUX_SIGN_IN)
        title = _TITLES[cluster]
        kind = "NERSC IRI" if cluster == "perlmutter" else "S3M"
        token = {
            "odo": user.odo_s3m_token,
            "frontier": user.frontier_s3m_token,
            "perlmutter": user.nersc_iri_token,
        }[cluster]
        if not token:
            return Check(
                ok=False,
                reason="not_connected",
                message=f"No {kind} token is saved for {title}.",
            )
        compute_url = f"{self._iri_url(cluster)}/api/v1/compute/resources"

        if cluster == "perlmutter":
            return self._from_iri(
                title, kind, await self._get_status(client, compute_url, token)
            )

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

    async def _globus(self, cluster: Literal["odo", "frontier"], user: Any) -> Check:
        title = _TITLES[cluster]
        source = globus_source(cluster, user)
        if source is None:
            return Check(
                ok=False,
                reason="not_connected",
                message=f"Globus file transfer is not connected for {title}.",
            )
        collection = (
            self._settings.odo_globus_collection_id
            if cluster == "odo"
            else self._settings.frontier_globus_collection_id
        )
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    self._globus_probe,
                    transfer=source.transfer,
                    https=source.https,
                    collection_id=collection,
                    client_id=self._settings.globus_native_app_client_id,
                ),
                GLOBUS_TIMEOUT,
            )
        except GlobusSessionExpired:
            return Check(
                ok=False,
                reason="session_expired",
                message=f"The Globus session for {title} has expired; connect Globus again.",
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


hpc_status_service = HpcStatusService()
