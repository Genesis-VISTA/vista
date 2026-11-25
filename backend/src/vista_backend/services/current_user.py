"""
Who a request is from: the one place the backend decides it.

Desktop (`settings.deployment == "desktop"`): always the implicit user `local`,
no token needed.

Hosted: the access token in `Authorization: Bearer` (which the UI's proxy fills
from the gateway's `X-Access-Token`), verified here whatever the gateway already
checked: signature against the realm's keys, issuer, `typ` `Bearer`, an allowed
`azp`, the required client role. The user is the row for the token's `sub`,
made on its first verified request. No token, or a bad one: `401`; no role:
`403`; the realm's keys unreachable: `503`.

Spec: openspec/changes/multi-user-hosted/specs/user-accounts/spec.md.
Prior art: collaboration_server/src/vista_collaboration_server/services/identity.py
(`TokenVerifier`, `get_user`), and `git show 301fba76^:backend/src/vista_backend/services/auth.py`.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta
from functools import cache
from typing import Annotated as A, Any

import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.concurrency import run_in_threadpool

from ..config import settings
from ..db.db import SessionDep
from ..db.schemas import LOCAL_USER_ID, UserTable
from ..utils.misc import now_iso

SEEN_EVERY = timedelta(minutes=5)
"""
How stale `last_seen_at` may get before a request rewrites it. SQLite has one
writer, so a write on every request would queue every request behind it.
"""


async def ensure_local(session: AsyncSession) -> UserTable:
    """The implicit user `local`, created if missing. `init_db` calls it on every start."""
    row = await session.get(UserTable, LOCAL_USER_ID)
    if row is None:
        row = UserTable(id=LOCAL_USER_ID, sub=None, created_at=now_iso())
        session.add(row)
        await session.flush()
    return row


class TokenVerifier:
    """
    Checks a hosted deployment's access tokens.

    `issuer`, `allowed_azp` and `required_role` ("<client>/<role>") come from
    `settings.oidc_*`. `keys`, a JWKS document, skips fetching the realm's keys,
    for tests; without it the keys come from
    `{issuer}/protocol/openid-connect/certs`.
    """

    def __init__(
        self,
        issuer: str,
        allowed_azp: list[str],
        required_role: str,
        *,
        keys: dict[str, Any] | None = None,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.allowed_azp = set(allowed_azp)
        self.role_client, _, self.role = required_role.partition("/")
        self._keys = jwt.PyJWKSet.from_dict(keys) if keys else None
        self._client = jwt.PyJWKClient(
            f"{self.issuer}/protocol/openid-connect/certs", timeout=10
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
        The token's claims. Raises `HTTPException`: 401 for a token this
        backend does not accept (expired, bad signature, wrong issuer, not
        `typ` Bearer, `azp` not allowed), 403 without the role, 503 when the
        realm's keys cannot be fetched.
        """
        try:
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
                detail="This account has not been given access to this VISTA.",
            )
        return claims


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


@cache
def get_verifier() -> TokenVerifier:
    """
    The process's verifier, built once from `settings`. Tests replace this
    function (`monkeypatch.setattr(current_user, "get_verifier", ...)`).
    """
    return TokenVerifier(
        settings.oidc_issuer, settings.oidc_allowed_azp, settings.oidc_required_role
    )


def bearer_token(authorization: str | None) -> str | None:
    """The token in an `Authorization: Bearer` header, if there is one."""
    scheme, _, token = (authorization or "").partition(" ")
    return (token.strip() or None) if scheme.lower() == "bearer" else None


def _stale(seen: str | None, now: datetime) -> bool:
    if seen is None:
        return True
    try:
        return now - datetime.fromisoformat(seen) >= SEEN_EVERY
    except ValueError:
        return True


async def _by_sub(session: AsyncSession, sub: str) -> UserTable | None:
    return (await session.exec(select(UserTable).where(UserTable.sub == sub))).first()


class NoUserContext(RuntimeError):
    """Hosted code that needs a user ran outside a request, with none set."""


_current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)
""" Who the code running now acts for; set by `get_current_user` and `use_user`. """


_request_access_token: ContextVar[str | None] = ContextVar(
    "request_access_token", default=None
)
"""
The raw access token of the hosted request being served, for calls made on the
researcher's behalf (the collaboration server, D11). Set and reset by
`get_current_user` only: never stored, never logged, and `use_user` does not
carry it, so background work has none.
"""


def request_access_token() -> str | None:
    """The running request's access token; `None` outside a hosted request."""
    return _request_access_token.get()


@contextmanager
def use_user(user_id: str) -> Iterator[None]:
    """
    Act as `user_id` for the block: what `get_current_user` does for a request,
    for code that has none. Group 7's `as_user` wraps this.

    It also drops the request's access token for the block: a task started in
    it (a woken turn, a job watch) copies the context and outlives the request,
    and may act for someone other than the request's user, so it must carry no
    token of theirs (D11: background work has none).
    """
    token = _current_user_id.set(user_id)
    no_access_token = _request_access_token.set(None)
    try:
        yield
    finally:
        _request_access_token.reset(no_access_token)
        _current_user_id.reset(token)


def as_user(user_id: str | None):
    """
    Background work's entry: run the block as the user whose row it acts on
    (`inherited_owner`, or the row's `user_id`). A row with no owner (NULL, a
    pre-ownership one) is `local`'s.
    """
    return use_user(user_id or LOCAL_USER_ID)


def current_user_id() -> str:
    """
    The user the running code acts for: the request's, or the one `use_user`
    set. With neither, a desktop install acts as `local` (it has no other), and
    a hosted one raises: whose credentials and settings it would use is not
    something to guess.
    """
    user_id = _current_user_id.get()
    if user_id is not None:
        return user_id
    if settings.deployment != "hosted":
        return LOCAL_USER_ID
    raise NoUserContext(
        "No user is set for this code: on a hosted deployment, background work "
        "must run as the user whose row it acts on (`use_user`)."
    )


async def get_current_user(
    session: SessionDep,
    authorization: A[str | None, Header()] = None,
) -> AsyncIterator[UserTable]:
    """
    The current user, per the module docstring, also set as `current_user_id()`
    for everything the request runs (both deployments). The previous value comes
    back when the request ends, so a caller that shares one context across
    requests (a test client) is not left acting as the last user.
    """
    user = await _resolve_user(session, authorization)
    previous = _current_user_id.get()
    _current_user_id.set(user.id)
    previous_token = _request_access_token.get()
    _request_access_token.set(
        bearer_token(authorization) if settings.deployment == "hosted" else None
    )
    try:
        yield user
    finally:
        _current_user_id.set(previous)
        _request_access_token.set(previous_token)


async def _resolve_user(session: AsyncSession, authorization: str | None) -> UserTable:
    """
    Hosted: get-or-create the row for the token's `sub`, refreshing
    `display_name`, `email` and `last_seen_at`.
    """
    if settings.deployment != "hosted":
        # A desktop install has one user; a stray token changes nothing.
        return await session.get(UserTable, LOCAL_USER_ID) or await ensure_local(
            session
        )

    token = bearer_token(authorization)
    if token is None:
        raise _unauthorized("Sign in to use VISTA.")
    claims = await get_verifier().verify(token)
    sub = str(claims["sub"])
    name = claims.get("name") or claims.get("preferred_username")
    email = claims.get("email")
    now = datetime.fromisoformat(now_iso())

    row = await _by_sub(session, sub)
    if row is None:
        row = UserTable(
            sub=sub,
            display_name=name,
            email=email,
            created_at=now.isoformat(),
            last_seen_at=now.isoformat(),
        )
        try:
            # A savepoint: two first requests for one sub race to insert, and
            # the loser takes the winner's row instead of failing the request.
            async with session.begin_nested():
                session.add(row)
        except IntegrityError:
            row = await _by_sub(session, sub)
            if row is None:
                raise
            return row
        # Only the request that made the user: the race's loser takes a row
        # whose projects the winner is making.
        from .user_provisioning import provision_new_user

        await provision_new_user(session, row)
        return await _committed(session, row)

    if row.display_name != name or row.email != email or _stale(row.last_seen_at, now):
        row.display_name = name
        row.email = email
        row.last_seen_at = now.isoformat()
        return await _committed(session, row)
    return row


async def _committed(session: AsyncSession, row: UserTable) -> UserTable:
    """
    Commit the user's row now, before the route runs. The request's session
    closes only after the whole response is sent, so an uncommitted write here
    would hold SQLite's one write lock for as long as a chat stream lasts.
    """
    await session.commit()
    await session.refresh(row)
    return row


CurrentUser = A[UserTable, Depends(get_current_user)]
""" What a route takes to know who is asking. Attached router-wide in `api/api.py` (task 4.2). """
