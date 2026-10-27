"""
Who is asking: the one place authentication lives.

With `auth_mode = "oidc"` (the default, and what a deployment runs), a caller
sends an access token from the configured OIDC realm as `Authorization:
Bearer`, and this module checks it: signature (against the realm's published
keys), issuer, expiry, client and the required role. A login gateway in front
of the server may check it first; nothing here relies on that. The token is
what `GET /sign-in` shows a researcher to paste into VISTA.

With `auth_mode = "dev-headers"`, for development and tests, a VISTA backend
names itself with two headers, its install id and its researcher's display
name, and is believed. That is no security at all, which is why the server
will only listen on loopback with it.

Either way a route gets a `User`. Every signed-in user may read and write
everything; who may change what is decided later, from `User.sub`.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated as A, Any
from urllib.parse import unquote

import jwt
from fastapi import Depends, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from ..config import CollabSettings

INSTALL_ID_HEADER = "X-Vista-Install-Id"
DISPLAY_NAME_HEADER = "X-Vista-Display-Name"
"""
Percent-encoded UTF-8: a header value is Latin-1 at best, and a researcher's
name need not be.
"""


@dataclass(frozen=True)
class User:
    sub: str
    """ Who: the token's `sub`, stable for a person. In dev-headers mode, the install id. """
    email: str | None
    name: str | None
    """ For showing; never used to decide anything. """
    install_id: uuid.UUID | None
    """ The VISTA installation the request says it came from: unchecked, a note only. """


class TokenVerifier:
    """
    Checks access tokens from the realm. Its keys come from the realm's JWKS
    endpoint through PyJWT's `PyJWKClient`, which caches them and fetches
    again (with a cooldown) when a token names one it does not have. `keys`
    (a JWKS document) skips fetching, for tests.
    """

    def __init__(self, settings: CollabSettings, *, keys: dict[str, Any] | None = None):
        self.issuer = settings.oidc_issuer.rstrip("/")
        self.allowed_azp = set(settings.oidc_allowed_azp)
        self.role_client, _, self.role = settings.oidc_required_role.partition("/")
        self._keys = jwt.PyJWKSet.from_dict(keys) if keys else None
        self._client = jwt.PyJWKClient(
            f"{self.issuer}/protocol/openid-connect/certs", cache_keys=True, timeout=10
        )

    def _signing_key(self, token: str) -> jwt.PyJWK:
        if self._keys is None:
            return self._client.get_signing_key_from_jwt(token)
        kid = jwt.get_unverified_header(token).get("kid")
        for key in self._keys.keys:
            if key.key_id == kid:
                return key
        raise jwt.InvalidTokenError("signed with an unknown key")

    async def verify(self, token: str) -> dict[str, Any]:
        """
        The token's claims. Raises `HTTPException`: 401 for a token this server
        does not accept, 403 for one without the role, 503 when the realm's
        keys cannot be fetched (which says nothing about the token).
        """
        try:
            # `PyJWKClient` fetches with urllib: off the event loop.
            key = await run_in_threadpool(self._signing_key, token)
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"],
                issuer=self.issuer,
                # Keycloak's `aud` varies; `azp` names the client the token is for.
                options={"verify_aud": False, "require": ["exp", "iss", "sub"]},
                leeway=30,
            )
        except jwt.PyJWKClientConnectionError:
            raise HTTPException(
                status_code=503,
                detail="This server cannot check tokens right now: the sign-in "
                "realm's keys are unreachable. Try again shortly.",
            )
        except jwt.ExpiredSignatureError:
            raise _unauthorized("The token has expired; get a new one.")
        except jwt.PyJWTError:
            raise _unauthorized("The token is not valid; get a new one.")
        if claims.get("typ") != "Bearer" or claims.get("azp") not in self.allowed_azp:
            # An ID token, or one for another client, is signed by the same realm.
            raise _unauthorized("The token is not an access token for this server.")
        roles = (
            claims.get("resource_access", {}).get(self.role_client, {}).get("roles", [])
        )
        if self.role not in roles:
            raise HTTPException(
                status_code=403,
                detail="This account has not been given access to this collaboration server.",
            )
        return claims


def _install_id(value: str | None) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"{INSTALL_ID_HEADER} must be a UUID."
        )


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


def bearer_token(authorization: str | None) -> str | None:
    """The token in an `Authorization: Bearer` header, if there is one."""
    scheme, _, token = (authorization or "").partition(" ")
    return (token.strip() or None) if scheme.lower() == "bearer" else None


async def get_user(
    request: Request,
    authorization: A[str | None, Header()] = None,
    install_id: A[str | None, Header(alias=INSTALL_ID_HEADER)] = None,
    display_name: A[str | None, Header(alias=DISPLAY_NAME_HEADER)] = None,
) -> User:
    """The caller, or `401` when it does not prove who it is."""
    settings: CollabSettings = request.app.state.settings
    if settings.auth_mode == "dev-headers":
        install = _install_id(install_id)
        if install is None:
            raise _unauthorized(f"Send {INSTALL_ID_HEADER} to use this server.")
        name = unquote(display_name).strip() if display_name else ""
        return User(sub=str(install), email=None, name=name or None, install_id=install)

    token = bearer_token(authorization)
    if token is None:
        raise _unauthorized(
            "Sign in: get a token from this server's /sign-in page and send it "
            "as a Bearer token."
        )
    verifier: TokenVerifier = request.app.state.verifier
    claims = await verifier.verify(token)
    # Read only once the token has said who this is, and only as a note: a
    # malformed one is dropped rather than refusing a signed-in caller.
    try:
        install = _install_id(install_id)
    except HTTPException:
        install = None
    return User(
        sub=str(claims["sub"]),
        email=claims.get("email"),
        name=claims.get("name") or claims.get("preferred_username"),
        install_id=install,
    )


UserDep = A[User, Depends(get_user)]
