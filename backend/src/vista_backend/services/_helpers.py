from typing import Literal
from ..db.schemas import UserPublicWithConfig

ServiceUser = UserPublicWithConfig | Literal["system"]
"""
Caller identity for service-layer access checks.
The ``"system"`` sentinel marks a trusted internal call and bypasses all access checks. Any real
user is checked normally. There is deliberately no ``None`` option, so callers must opt into the
bypass explicitly rather than have a missing user silently grant access.
"""
