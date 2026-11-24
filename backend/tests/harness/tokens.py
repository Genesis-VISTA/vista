"""
A stand-in Keycloak for backend tests: a signing key, its JWKS, and access
tokens shaped like Obsidian's realm (`azp` apisix, role `apisix/vista`, `typ`
Bearer). Mirrors `vista_collaboration_server.testing.FakeIssuer`, without
depending on the collaboration server's settings.
"""

import time
import uuid
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "https://keycloak.test/realms/obsidian"
AZP = "apisix"
ROLE = "apisix/vista"


class FakeIssuer:
    def __init__(self, issuer: str = ISSUER, kid: str = "test-key"):
        self.issuer = issuer
        self.kid = kid
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwks(self) -> dict[str, Any]:
        public = RSAAlgorithm.to_jwk(self._key.public_key(), as_dict=True)
        return {"keys": [{**public, "kid": self.kid, "use": "sig", "alg": "RS256"}]}

    def token(
        self,
        sub: str = "a5b1c0de-0000-4000-8000-000000000001",
        *,
        name: str | None = "Ada Lovelace",
        email: str | None = "ada@ornl.gov",
        roles: list[str] | None = None,
        expires_in: int = 3600,
        **claims: Any,
    ) -> str:
        """An access token for `sub`; any claim (`iss`, `azp`, `typ`, …) can be overridden."""
        client, _, role = ROLE.partition("/")
        now = int(time.time())
        body: dict[str, Any] = {
            "iss": self.issuer,
            "sub": sub,
            "azp": AZP,
            "typ": "Bearer",
            "iat": now,
            "exp": now + expires_in,
            "jti": str(uuid.uuid4()),
            "resource_access": {client: {"roles": [role] if roles is None else roles}},
            "preferred_username": "ada",
        }
        if name is not None:
            body["name"] = name
        if email is not None:
            body["email"] = email
        body.update(claims)
        return jwt.encode(body, self._key, algorithm="RS256", headers={"kid": self.kid})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
