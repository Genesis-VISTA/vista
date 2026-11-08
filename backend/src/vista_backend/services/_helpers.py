import uuid
from ..config import settings


def new_storage_path() -> str:
    """
    Generate a fresh, unique on-disk location under `settings.storage_dir` for
    storing arbitrary files (e.g. a skill folder), as a path relative to
    `settings.data_dir` (e.g. `storage/{uuid}`) suitable for storing in the db.
    """
    return (
        (settings.storage_dir / str(uuid.uuid4()))
        .relative_to(settings.data_dir)
        .as_posix()
    )
