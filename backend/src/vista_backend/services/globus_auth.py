"""Connecting a researcher's Globus account, in two calls.

This is `scripts/get_globus_token.py`'s flow with the terminal taken out. That
script starts a native-app flow, prints the authorization address, reads a code
from `input()`, and exchanges it. Everything survives the move to a web
interface except `input()`.

What does not survive automatically is PKCE. The flow generates a code verifier,
sends only its hash to Globus, and must present the verifier itself when
exchanging the code, which proves the exchange comes from whoever asked for the
address. In a script that is one process and a local variable. Here the address
and the exchange are separate requests, so the verifier is held between them --
server side, never sent to the browser. A verifier the client carried would make
the code sufficient on its own, which is the attack PKCE exists to prevent.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from typing import Literal

import globus_sdk
from fastapi import HTTPException
from globus_sdk.exc import GlobusAPIError

Cluster = Literal["odo", "frontier"]

CLIENT_ID = "fae5c579-490a-4d76-b6eb-d78f65caeb63"
"""The same native-app client `scripts/get_globus_token.py` uses, so a
researcher who has consented once is not asked again by a second client."""

TRANSFER_RESOURCE_SERVER = "transfer.api.globus.org"
AUTH_RESOURCE_SERVER = "auth.globus.org"

BASE_SCOPES = (
    "openid",
    "profile",
    "email",
    "urn:globus:auth:scope:auth.globus.org:view_identities",
    f"urn:globus:auth:scope:{TRANSFER_RESOURCE_SERVER}:all",
)
"""Everything that is the same for both clusters.

Transfer is here because directory listings and `mkdir` still go through it;
the HTTPS interface offers neither. The bytes themselves need the collection's
own scope, which is per-cluster and so is added by `scopes_for`.
"""

COLLECTION_ID_ENV_VARS: dict[Cluster, str] = {
    "odo": "VISTA_MCP_ODO_GLOBUS_COLLECTION_ID",
    "frontier": "VISTA_MCP_FRONTIER_GLOBUS_COLLECTION_ID",
}
"""The MCP server's own settings names for these, read here too.

The two services are separate installs and the backend does not depend on the
MCP server's package, so the defaults below are duplicated rather than
imported. But the duplication has to be able to stay in step: the token minted
here carries a scope naming one collection, and the MCP server sends the
request to whichever collection *it* is configured for. If those two ever
disagreed every request would come back 401, and VISTA would keep telling the
researcher to reconnect a credential that was never the problem.

So the same environment variable feeds both. A deployment that overrides one
overrides the other by construction, because there is only one.
"""

_DEFAULTS: dict[Cluster, str] = {
    "odo": "7399956e-a57b-4560-b3d7-a035ff42cad4",
    "frontier": "36d521b3-c182-4071-b7d5-91db5d380d42",
}
"""Matches `vista_mcp_server.config`'s defaults. Properties of OLCF's
deployment rather than of VISTA's configuration -- they change only if OLCF
rebuilds a collection."""


def collection_id(cluster: Cluster) -> str:
    """Which collection this cluster's `/https` scope is for.

    Read on each use rather than captured at import, so it does not depend on
    the environment having been loaded before this module was.
    """
    return os.environ.get(COLLECTION_ID_ENV_VARS[cluster]) or _DEFAULTS[cluster]


def scopes_for(cluster: Cluster) -> tuple[str, ...]:
    """What to ask Globus for, for one cluster.

    The per-collection `/https` scope is what authorizes reading and writing
    file contents over the HTTPS interface, and it embeds the collection's own
    UUID -- which is why the scope set cannot be one module-level constant
    shared by both enclaves.

    No `data_access`. High-assurance GCSv5 mapped collections reject it; their
    session requirement is what replaces it.
    """
    return (
        *BASE_SCOPES,
        f"https://auth.globus.org/scopes/{collection_id(cluster)}/https",
    )


CLUSTER_SESSION_DOMAINS: dict[Cluster, str] = {
    "odo": "opensso.ccs.ornl.gov",
    "frontier": "sso.ccs.ornl.gov",
}
"""The two OLCF enclaves authenticate against different identity providers, so
the authorization address pins one. Same values as `get_globus_token.py`."""

TOKEN_FIELDS: dict[Cluster, str] = {
    "odo": "odo_globus_token",
    "frontier": "frontier_globus_token",
}
"""Where each cluster's Transfer refresh token is stored."""

HTTPS_TOKEN_FIELDS: dict[Cluster, str] = {
    "odo": "odo_globus_https_token",
    "frontier": "frontier_globus_https_token",
}
"""And its collection token. Two columns rather than one blob, because a name
that can be grepped is worth more here than a migration that never comes."""

PENDING_TTL_SECONDS = 15 * 60
"""How long an address stays exchangeable. Long enough to log in unhurried,
short enough that an abandoned flow does not sit in memory for a day.

It is also what bounds a flow that keeps being handed bad codes, since a
refusal on its own does not end one -- see `complete_login`."""


@dataclass(frozen=True)
class Connection:
    """What a completed authorization yields."""

    refresh_token: str
    """Transfer's, for directory listings and `mkdir`."""
    https_refresh_token: str
    """The collection's, for the file contents themselves.

    A second token because Globus issues one per resource server and the
    collection is its own. Both are stored: half a credential can list a
    directory and read nothing in it, which is indistinguishable from an empty
    output directory -- the confusion this whole transport exists to remove.
    """
    identity: str
    """Who Globus says authorized this. Shown so a researcher can tell their two
    enclave identities apart, and never taken from what they typed."""


@dataclass
class _Pending:
    verifier: str
    started_at: float


_pending: dict[tuple[uuid.UUID, Cluster], _Pending] = {}
"""Flows waiting for a code, keyed by whose they are.

In memory, which ties a pending flow to one backend process: a second worker
would not find it and the researcher would be told to start again. The desktop
runs one process, and the cost of being wrong is a repeated login rather than a
lost credential, so this is not worth a table.
"""


def _client() -> globus_sdk.NativeAppAuthClient:
    return globus_sdk.NativeAppAuthClient(CLIENT_ID)


def _prune(now: float) -> None:
    for key, pending in list(_pending.items()):
        if now - pending.started_at > PENDING_TTL_SECONDS:
            del _pending[key]


def start_login(user_id: uuid.UUID, cluster: Cluster) -> str:
    """Begin a flow and return the address the researcher authorizes at.

    Starting again replaces any flow already pending for this researcher and
    cluster. Two live addresses for one connection would differ only in their
    verifier, and a code from the older one would then fail with a PKCE error
    that describes nothing the researcher did wrong.
    """
    now = time.time()
    _prune(now)

    client = _client()
    flow = client.oauth2_start_flow(
        requested_scopes=" ".join(scopes_for(cluster)),
        refresh_tokens=True,
        prefill_named_grant=f"VISTA ({cluster})",
    )
    _pending[(user_id, cluster)] = _Pending(verifier=flow.verifier, started_at=now)

    return client.oauth2_get_authorize_url(
        session_required_single_domain=CLUSTER_SESSION_DOMAINS[cluster],
    )


def complete_login(user_id: uuid.UUID, cluster: Cluster, code: str) -> Connection:
    """Exchange the code the researcher pasted for a long-lived credential."""
    code = code.strip()
    if not code:
        raise HTTPException(400, "Paste the code Globus showed you after logging in.")

    pending = _pending.get((user_id, cluster))
    if pending is None or time.time() - pending.started_at > PENDING_TTL_SECONDS:
        _pending.pop((user_id, cluster), None)
        raise HTTPException(
            400,
            "This connection attempt has expired. Start it again to get a fresh "
            "address, and use the code that one gives you.",
        )

    client = _client()
    # The same flow, rebuilt around the verifier that produced the address.
    client.oauth2_start_flow(
        requested_scopes=" ".join(scopes_for(cluster)),
        refresh_tokens=True,
        verifier=pending.verifier,
    )

    try:
        response = client.oauth2_exchange_code_for_tokens(code)
    except GlobusAPIError as error:
        # The flow deliberately stays pending. The usual reason a code is
        # refused is that it was half-copied, and the same address with the
        # whole code still works -- so taking the flow away here would turn a
        # typo into a repeat of the entire login. Expiry still bounds it.
        raise _readable(error) from error
    except globus_sdk.GlobusError as error:
        # A malformed code never reaches Globus; the SDK rejects it first.
        raise HTTPException(400, f"That code was not accepted: {error}") from error

    # Accepted, so the code is spent and the flow it belonged to is finished
    # with -- including on the failures below, which are about what came back
    # rather than about the exchange.
    _pending.pop((user_id, cluster), None)

    transfer = _refresh_token(
        response.data,
        TRANSFER_RESOURCE_SERVER,
        "Globus Transfer, which VISTA uses to list the cluster's directories",
    )
    https = _refresh_token(
        response.data,
        collection_id(cluster),
        f"{cluster.title()}'s storage, which VISTA uses to read and write files",
    )

    return Connection(
        refresh_token=transfer,
        https_refresh_token=https,
        identity=_identity(response),
    )


def _refresh_token(data: dict, resource_server: str, what: str) -> str:
    """One resource server's lasting credential out of the exchange response.

    Globus returns the token for one resource server at the top level and the
    rest under `other_tokens`, so which one arrives where depends on the order
    the scopes were granted in, not on anything worth relying on. With two
    resource servers now in play, reading only the top level would quietly keep
    whichever Globus happened to put there.
    """
    token = None
    if data.get("resource_server") == resource_server:
        token = data
    else:
        for candidate in data.get("other_tokens", []):
            if candidate.get("resource_server") == resource_server:
                token = candidate
                break
    if token is None:
        raise HTTPException(
            502,
            f"Globus did not return a credential for {what}. The login may have "
            "been completed without granting VISTA that access.",
        )
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise HTTPException(
            502,
            "Globus returned a credential that expires in an hour rather than a "
            "lasting one. Start the connection again.",
        )
    return refresh_token


def _identity(response: globus_sdk.OAuthTokenResponse) -> str:
    """Whose account this is, from the id_token Globus signed.

    Read rather than asked for, so what the interface shows is the identity the
    login actually produced. That matters here: the researcher authorizes each
    enclave separately and needs to see which account each one landed on.
    """
    try:
        claims = response.decode_id_token()
    except Exception:
        # The identity is a courtesy. A credential that works is the point, and
        # losing the label is not a reason to fail the connection.
        return "unknown"
    return (
        claims.get("preferred_username")
        or claims.get("email")
        or claims.get("name")
        or "unknown"
    )


def _readable(error: GlobusAPIError) -> HTTPException:
    """Globus's refusals, as something a researcher can act on.

    The common one by far is a code pasted from an address other than the one
    just issued, which Globus reports as a verifier mismatch. Left untranslated
    it reads as a fault in VISTA, and the thing that fixes it -- closing the old
    Globus tabs -- is not something the message suggests.
    """
    # `text` is the response body. `str(error)` is a tuple of request metadata
    # that never contains Globus's reason, and `raw_text` is the globus_sdk 3.x
    # spelling that 4.x dropped -- reading either would make this test below
    # never match, which is how it was written the first time.
    body = getattr(error, "text", "") or ""
    mismatch = "code_verifier does not match" in body or (
        "invalid_grant" in body and error.http_status == 400
    )
    if mismatch:
        return HTTPException(
            400,
            "That code does not match this connection attempt. It usually means "
            "the code came from an older Globus tab. Close any Globus login tabs "
            "still open, start the connection again, and use only the address it "
            "gives you.",
        )
    return HTTPException(
        502,
        f"Globus refused the connection (HTTP {error.http_status}). "
        "Start it again, and if it keeps failing the Globus service status is "
        "worth checking.",
    )
