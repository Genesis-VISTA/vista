import uuid
from typing import Literal
from ..config import settings
from ..db.schemas import UserPublicWithConfig

ServiceUser = UserPublicWithConfig | Literal["system"]
"""
Caller identity for service-layer access checks.
The ``"system"`` sentinel marks a trusted internal call and bypasses all access checks. Any real
user is checked normally. There is deliberately no ``None`` option, so callers must opt into the
bypass explicitly rather than have a missing user silently grant access.
"""


def new_storage_path() -> str:
    """
    Generate a fresh, unique on-disk location under `settings.storage_dir` for
    storing arbitrary files (e.g. a skill folder), as a path relative to
    `settings.data_dir` (e.g. `storage/{uuid}`) suitable for storing in the db.
    """
    return (settings.storage_dir / str(uuid.uuid4())).relative_to(settings.data_dir).as_posix()
