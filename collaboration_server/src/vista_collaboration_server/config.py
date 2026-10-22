from pathlib import Path
from typing import Annotated as A, Literal

from pydantic import AfterValidator, BeforeValidator, ByteSize, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# `ResolvedPath`, `LogLevel` and `_ENV_FILES` are copies of the backend's
# (`vista_backend/utils/types.py`, `vista_backend/config.py`), not imports: this
# server must not depend on the backend. Keep them in step.


def _resolve(path: str | Path) -> Path:
    return (Path.cwd() / Path(path).expanduser()).resolve()


ResolvedPath = A[Path, AfterValidator(_resolve)]

LogLevel = A[
    Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"],
    BeforeValidator(lambda s: s.upper() if isinstance(s, str) else s),
]

# Every `.env` from the filesystem root down to the cwd, nearest last so the
# most-specific file wins, as the backend reads them. Only `VISTA_COLLAB_*`
# names are read from them.
_ENV_FILES: list[Path] = [
    p / ".env" for p in reversed([Path.cwd(), *Path.cwd().parents])
]


class CollabSettings(BaseSettings):
    """
    The collaboration server's configuration, from `VISTA_COLLAB_*` variables.

    Passed to `create_app` rather than read from a module global, so a test (or
    a backend test mounting this app in-process) can give each app its own data
    directory.
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES, extra="ignore", env_prefix="VISTA_COLLAB_"
    )

    host: str = "127.0.0.1"
    """
    Where the server listens. Loopback by default: there is no authentication
    yet, so anyone who can reach the server can read and write everything on it.
    """

    port: int = 8002

    log_level: LogLevel = "INFO"

    data_dir: ResolvedPath = Path("../data/collaboration-server")
    """
    The server's own data: its database and its blobs. Relative paths resolve
    against the working directory; the default, from `collaboration_server/`,
    is the checkout's ignored `data/` directory, beside the backend's.
    """

    database_url: A[
        str,
        Field(
            default_factory=lambda data: (
                "sqlite+aiosqlite:///"
                f"{(data['data_dir'] / 'collaboration.db').resolve()}"
            )
        ),
    ]
    """ SQLAlchemy async database URL. Defaults to SQLite in `data_dir`. """

    max_blob_size: ByteSize = ByteSize(2 * 1024**3)
    """ The largest blob `POST /blobs` accepts, in bytes. """

    @property
    def blobs_dir(self) -> Path:
        """
        Where uploaded blobs live, each named by its SHA-256 (`<first two hex
        digits>/<digest>`), so the same bytes are stored once and a stored blob
        never changes.
        """
        return self.data_dir / "blobs"
