"""
A stand-in Keycloak for tests: a signing key, its JWKS, and tokens shaped like
the realm's. Here rather than under `tests/` so the backend's tests, which
mount this server in-process, can sign in to it too.
"""

import time
import uuid
from typing import Any

import jwt
from jwt.algorithms import RSAAlgorithm
from cryptography.hazmat.primitives.asymmetric import rsa

from .config import CollabSettings
from .services.identity import TokenVerifier


class FakeIssuer:
    """Signs tokens the way Obsidian's Keycloak would, for `settings`' realm."""

    def __init__(self, settings: CollabSettings, kid: str = "test-key"):
        self.settings = settings
        self.kid = kid
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwks(self) -> dict[str, Any]:
        public = RSAAlgorithm.to_jwk(self._key.public_key(), as_dict=True)
        return {"keys": [{**public, "kid": self.kid, "use": "sig", "alg": "RS256"}]}

    def verifier(self) -> TokenVerifier:
        """A verifier that knows this issuer's key without fetching it."""
        return TokenVerifier(self.settings, keys=self.jwks())

    def token(
        self,
        sub: str = "a5b1c0de-0000-4000-8000-000000000001",
        *,
        email: str | None = "ada@ornl.gov",
        name: str | None = "Ada Lovelace",
        roles: list[str] | None = None,
        expires_in: int = 3600,
        kid: str | None = None,
        **claims: Any,
    ) -> str:
        """
        An access token for `sub`. `roles` are the role client's roles, the
        required one by default; any other claim (`iss`, `azp`, …) can be
        overridden through `claims`.
        """
        client, _, role = self.settings.oidc_required_role.partition("/")
        now = int(time.time())
        body: dict[str, Any] = {
            "iss": self.settings.oidc_issuer.rstrip("/"),
            "sub": sub,
            "azp": self.settings.oidc_allowed_azp[0],
            "typ": "Bearer",
            "iat": now,
            "exp": now + expires_in,
            "jti": str(uuid.uuid4()),
            "resource_access": {client: {"roles": [role] if roles is None else roles}},
            "preferred_username": "ada",
        }
        if email is not None:
            body["email"] = email
        if name is not None:
            body["name"] = name
        body.update(claims)
        return jwt.encode(
            body, self._key, algorithm="RS256", headers={"kid": kid or self.kid}
        )
