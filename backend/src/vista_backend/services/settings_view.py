"""
The settings as code reads them: the installation's server settings and one
user's settings, merged (openspec change multi-user-hosted, design D3).

`LocalSettingsTable` keeps the installation's fields (server settings) and
`UserSettingsTable` holds each user's. A `SettingsView` reads and writes through
to whichever row owns a field, so code written against one settings row keeps
working: `view.email` is the user's, `view.collaboration_server_url` the
server's. On a hosted deployment the server settings come from the environment,
never the row.

Which user is `services/current_user.current_user_id()`: the request's, or
`local` outside one on a desktop install; hosted, outside a request, it raises.
"""

from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from ..config import settings
from ..db.schemas import (
    LOCAL_SETTINGS_ID,
    LOCAL_USER_ID,
    SECRET_SETTINGS_FIELDS,
    LocalSettingsTable,
    UserSettingsTable,
)
from .current_user import current_user_id
from .hosted import is_hosted

USER_FIELDS = frozenset(UserSettingsTable.model_fields) - {"user_id"}
""" The fields that belong to one user. Everything else on `LocalSettingsTable` is the server's. """

SERVER_FIELDS = frozenset(LocalSettingsTable.model_fields) - USER_FIELDS - {"id"}
""" The installation's fields (`id` is the row's key, not a setting). """

HOSTED_SERVER_VALUES: dict[str, Callable[[], Any]] = {
    "collaboration_server_url": lambda: settings.collaboration_server_url,
    "collaboration_enabled": lambda: settings.collaboration_enabled,
    # The configured endpoint's key is `Settings.openai_api_key`, which
    # `agents/inference` reads itself; the row's legacy copy is never used.
    "inference_api_key": lambda: None,
    # A hosted VISTA is updated by its operator (spec `hosted-mode`).
    "update_checks": lambda: False,
    "update_prereleases": lambda: False,
}
"""
Server settings a hosted deployment takes from the environment instead of the
row, and what they read there. The others (`install_id`, `update_checked_at`,
the unused `collaboration_team_id`) are the installation's own records, not
configuration.
"""

SERVER_SETTINGS_REASON = (
    "These settings are set by the server: this deployment takes them from its "
    "environment, and they cannot be changed here."
)


def server_managed_fields() -> list[str]:
    """The fields the API cannot change right now: the environment's, when hosted."""
    return sorted(HOSTED_SERVER_VALUES) if is_hosted() else []


class SettingsView:
    """
    One installation's server settings and one user's, as one object. Reads and
    writes go to the row that owns the field; `has_<credential>` is whether the
    credential is set. Build one with `load` (or `services.local_settings.get`).
    """

    __slots__ = ("_server", "_user")

    def __init__(self, server: LocalSettingsTable, user: UserSettingsTable) -> None:
        object.__setattr__(self, "_server", server)
        object.__setattr__(self, "_user", user)

    @property
    def server_row(self) -> LocalSettingsTable:
        return self._server

    @property
    def user_row(self) -> UserSettingsTable:
        return self._user

    @property
    def user_id(self) -> str:
        return self._user.user_id

    def __getattr__(self, name: str) -> Any:
        if name in USER_FIELDS:
            return getattr(self._user, name)
        if is_hosted() and name in HOSTED_SERVER_VALUES:
            return HOSTED_SERVER_VALUES[name]()
        if name.startswith("has_") and name[4:] in SECRET_SETTINGS_FIELDS:
            return bool(getattr(self, name[4:]))
        return getattr(self._server, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in USER_FIELDS:
            setattr(self._user, name, value)
        else:
            setattr(self._server, name, value)


async def ensure_user_settings(
    session: AsyncSession, user_id: str
) -> UserSettingsTable:
    """
    The user's settings row, made if there is none. For `local` on a desktop
    install it is made from the researcher's fields on `local_settings`, once:
    an installation from before the split keeps every value it had (encrypted
    fields are read and written through the same `EncryptedStr`, so a token
    saved before still reads back). Nothing is copied again once the row exists.
    """
    row = await session.get(UserSettingsTable, user_id)
    if row is not None:
        return row
    row = UserSettingsTable(user_id=user_id)
    if user_id == LOCAL_USER_ID and not is_hosted():
        old = await session.get(LocalSettingsTable, LOCAL_SETTINGS_ID)
        if old is not None:
            for name in USER_FIELDS:
                setattr(row, name, getattr(old, name))
    try:
        # A savepoint: two first reads for one user race to insert, and the
        # loser takes the winner's row.
        async with session.begin_nested():
            session.add(row)
    except IntegrityError:
        existing = await session.get(UserSettingsTable, user_id)
        if existing is None:
            raise
        return existing
    return row


async def load(session: AsyncSession, user_id: str | None = None) -> SettingsView:
    """
    The settings of `user_id`, else of `current_user_id()`. Never creates the
    server row: `init_db` does, once, at startup, so concurrent reads cannot
    race each other into making two.
    """
    server = await session.get(LocalSettingsTable, LOCAL_SETTINGS_ID)
    if server is None:
        raise RuntimeError("The settings row is missing; init_db creates it.")
    user = await ensure_user_settings(session, user_id or current_user_id())
    return SettingsView(server, user)
