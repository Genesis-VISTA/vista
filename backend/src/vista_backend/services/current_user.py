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

from typing import Annotated as A, Any

from fastapi import Depends, Header
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.db import SessionDep
from ..db.schemas import LOCAL_USER_ID, UserTable
from ..utils.misc import now_iso


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
        raise NotImplementedError("Sam's carve-out (task 4.2)")

    async def verify(self, token: str) -> dict[str, Any]:
        """
        The token's claims. Raises `HTTPException`: 401 for a token this
        backend does not accept (expired, bad signature, wrong issuer, not
        `typ` Bearer, `azp` not allowed), 403 without the role, 503 when the
        realm's keys cannot be fetched.
        """
        raise NotImplementedError("Sam's carve-out (task 4.2)")


def get_verifier() -> TokenVerifier:
    """
    The process's verifier, built once from `settings`. Tests replace this
    function (`monkeypatch.setattr(current_user, "get_verifier", ...)`).
    """
    raise NotImplementedError("Sam's carve-out (task 4.2)")


async def get_current_user(
    session: SessionDep,
    authorization: A[str | None, Header()] = None,
) -> UserTable:
    """
    The current user, per the module docstring. Hosted: get-or-create the row
    for the token's `sub`, refreshing `display_name`, `email` and
    `last_seen_at`.
    """
    raise NotImplementedError("Sam's carve-out (task 4.2)")


CurrentUser = A[UserTable, Depends(get_current_user)]
""" What a route takes to know who is asking. Attached router-wide in `api/api.py` (task 4.2). """
