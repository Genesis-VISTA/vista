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

SCOPES = (
    "openid",
    "profile",
    "email",
    "urn:globus:auth:scope:auth.globus.org:view_identities",
    f"urn:globus:auth:scope:{TRANSFER_RESOURCE_SERVER}:all",
)
"""Exactly what `get_globus_token.py` requests, and no more.

Creating the researcher's Globus Connect Personal collection needs no further
consent: probed against real Globus, a token carrying only these scopes created
an endpoint and returned its setup key. Globus Connect Personal's own setup asks
for `gcp_install`, which is why an earlier draft expected to need it too.
"""

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

PENDING_TTL_SECONDS = 15 * 60
"""How long an address stays exchangeable. Long enough to log in unhurried,
short enough that an abandoned flow does not sit in memory for a day."""


@dataclass(frozen=True)
class Connection:
    """What a completed authorization yields."""

    refresh_token: str
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
        requested_scopes=" ".join(SCOPES),
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
        requested_scopes=" ".join(SCOPES),
        refresh_tokens=True,
        verifier=pending.verifier,
    )

    try:
        response = client.oauth2_exchange_code_for_tokens(code)
    except GlobusAPIError as error:
        raise _readable(error) from error
    except globus_sdk.GlobusError as error:
        # A malformed code never reaches Globus; the SDK rejects it first.
        raise HTTPException(400, f"That code was not accepted: {error}") from error
    finally:
        # One code, one attempt. A spent or rejected code will not work twice,
        # so leaving the flow pending only invites a second identical failure.
        _pending.pop((user_id, cluster), None)

    transfer = _token_for(response.data, TRANSFER_RESOURCE_SERVER)
    refresh_token = transfer.get("refresh_token")
    if not refresh_token:
        raise HTTPException(
            502,
            "Globus returned a credential that expires in an hour rather than a "
            "lasting one. Start the connection again.",
        )

    return Connection(refresh_token=refresh_token, identity=_identity(response))


def _token_for(data: dict, resource_server: str) -> dict:
    """One resource server's token out of the exchange response.

    Globus returns the token for one resource server at the top level and the
    rest under `other_tokens`, so which one arrives where depends on the order
    the scopes were granted in, not on anything worth relying on.
    """
    if data.get("resource_server") == resource_server:
        return data
    for token in data.get("other_tokens", []):
        if token.get("resource_server") == resource_server:
            return token
    raise HTTPException(
        502,
        "Globus did not return a file-transfer credential. The login may have "
        "been completed without granting VISTA access to Globus Transfer.",
    )


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
