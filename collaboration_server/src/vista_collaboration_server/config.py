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
    """ Where the server listens. A deployment sets `0.0.0.0`. """

    port: int = 8002

    log_level: LogLevel = "INFO"

    auth_mode: Literal["oidc", "dev-headers"] = "oidc"
    """
    How a caller proves who it is (`services/identity.py`). `oidc`: a Keycloak
    access token, as `Authorization: Bearer`. `dev-headers`: the
    `X-Vista-Install-Id` and `X-Vista-Display-Name` headers, believed as sent,
    for development and tests only; the server refuses to start with it on
    anything but loopback.
    """

    oidc_issuer: str = "https://obsidian.ccs.ornl.gov/auth/realms/obsidian"
    """ The Keycloak realm whose tokens are accepted: their `iss`, and where its keys are. """

    oidc_allowed_azp: list[str] = ["apisix"]
    """
    The Keycloak clients a token may have been issued to (its `azp`). `apisix`
    is the client behind Obsidian's `/token`, which VISTA signs in through.
    """

    oidc_required_role: str = "apisix/vista"
    """
    The client role, as `<client>/<role>`, a token must carry (in its
    `resource_access`) to be let in: the same role Obsidian's gateway requires,
    checked again here so that a request that reaches this server some other
    way gets no further.
    """

    root_path: str = ""
    """
    The path prefix a proxy serves this server under and strips before
    forwarding (`/vista/api` behind Obsidian), so the docs and `openapi.json`
    link to the right place.
    """

    ssl_certfile: ResolvedPath | None = None
    """ With `ssl_keyfile`, serve HTTPS with this certificate. """

    ssl_keyfile: ResolvedPath | None = None

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
    """ The largest blob `POST /blobs` or `POST /blobs/stream` accepts, in bytes. """

    @property
    def blobs_dir(self) -> Path:
        """
        Where uploaded blobs live, each named by its SHA-256 (`<first two hex
        digits>/<digest>`), so the same bytes are stored once and a stored blob
        never changes.
        """
        return self.data_dir / "blobs"
