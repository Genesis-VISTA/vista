"""
Who is asking: a stub, and the one place authentication plugs in later.

A VISTA backend names itself with two headers, its install id and its
researcher's display name, and the server believes them. That is no security
at all, and is not meant to be: anyone who can reach the server can read and
write everything on it, which is why it listens on loopback by default. When
deployment is decided, this module verifies a credential instead, and every
route that depends on it is covered.
"""

import uuid
from dataclasses import dataclass
from typing import Annotated as A
from urllib.parse import unquote

from fastapi import Depends, Header, HTTPException

INSTALL_ID_HEADER = "X-Vista-Install-Id"
DISPLAY_NAME_HEADER = "X-Vista-Display-Name"
"""
Percent-encoded UTF-8: a header value is Latin-1 at best, and a researcher's
name need not be.
"""


@dataclass(frozen=True)
class Identity:
    install_id: uuid.UUID
    display_name: str | None


async def get_identity(
    install_id: A[str | None, Header(alias=INSTALL_ID_HEADER)] = None,
    display_name: A[str | None, Header(alias=DISPLAY_NAME_HEADER)] = None,
) -> Identity | None:
    """The caller's claimed identity, or `None` when it claims none."""
    if install_id is None:
        return None
    try:
        parsed = uuid.UUID(install_id)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"{INSTALL_ID_HEADER} must be a UUID."
        )
    name = unquote(display_name).strip() if display_name else ""
    return Identity(install_id=parsed, display_name=name or None)


async def require_identity(
    identity: A[Identity | None, Depends(get_identity)],
) -> Identity:
    """For a route that records who did it: refuses a caller that names no one."""
    if identity is None:
        raise HTTPException(
            status_code=401, detail=f"Send {INSTALL_ID_HEADER} to publish."
        )
    return identity


IdentityDep = A[Identity, Depends(require_identity)]
