"""
The install's Slack settings: the `slack_config` row and the two tokens.

The tokens live in the OS secret store (`utils/secret_store`), never in the
database or a file, and nothing here returns them to an API caller; the view
only says whether each is set.
"""

import re
from datetime import UTC, datetime

from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import SlackConfigTable
from ..utils import secret_store

BOT_TOKEN = re.compile(r"^xoxb-[A-Za-z0-9-]+$")
APP_TOKEN = re.compile(r"^xapp-[A-Za-z0-9-]+$")


class SlackConfigError(ValueError):
    """A setting that cannot be saved, with a reason to show the researcher."""


def now() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    return now().isoformat(timespec="seconds")


async def get_config(session: AsyncSession) -> SlackConfigTable:
    """The settings row, created with every default on first use."""
    row = await session.get(SlackConfigTable, 1)
    if row is None:
        row = SlackConfigTable(
            id=1, allowed_team_ids=[], allowed_channel_ids=[], lease_status=None
        )
        session.add(row)
        await session.flush()
    return row


def bot_token() -> str | None:
    return secret_store.get(secret_store.SLACK_BOT_TOKEN)


def app_token() -> str | None:
    return secret_store.get(secret_store.SLACK_APP_TOKEN)


def _check(value: str | None, pattern: re.Pattern, label: str) -> None:
    if value is None or value.strip() == "":
        return
    if not pattern.match(value.strip()):
        prefix = pattern.pattern[1:6]
        raise SlackConfigError(f"The {label} should start with {prefix}.")


def _set_token(name: str, value: str | None) -> None:
    """None keeps the token, "" removes it, anything else replaces it."""
    if value is None:
        return
    if value.strip() == "":
        secret_store.delete(name)
        return
    try:
        secret_store.put(name, value.strip())
    except secret_store.SecretStoreUnavailable as exc:
        raise SlackConfigError(
            f"Slack tokens are kept in the OS secret store, and there is none here: {exc}."
        ) from exc


def set_tokens(*, bot: str | None, app: str | None) -> None:
    """Both are checked before either is written."""
    _check(bot, BOT_TOKEN, "bot token")
    _check(app, APP_TOKEN, "app-level token")
    _set_token(secret_store.SLACK_BOT_TOKEN, bot)
    _set_token(secret_store.SLACK_APP_TOKEN, app)


def why_not_enabled(*, bot: str | None = None, app: str | None = None) -> str | None:
    """
    Why Slack cannot be switched on, or None when it can.

    `bot` and `app` are tokens about to be saved, with `set_tokens`'s meaning
    (None keeps the stored one, "" removes it), so a save is checked before
    anything is written.
    """
    ok, problem = secret_store.available()
    if not ok:
        return f"Slack needs the OS secret store for its tokens, and there is none here: {problem}."
    have_bot = bool(bot.strip()) if bot is not None else bool(bot_token())
    have_app = bool(app.strip()) if app is not None else bool(app_token())
    missing = [
        label
        for label, have in (("bot token", have_bot), ("app-level token", have_app))
        if not have
    ]
    if missing:
        return f"Slack needs its {' and '.join(missing)} first."
    return None
