"""
Which repository token a URL gets, and where it came from.

The one place that decides it, so skill import, `fetch_repository` and a project's
forum cannot drift apart. Each caller loads the settings row and passes it in;
this module only chooses. See openspec/changes/archive/2026-10-15-global-repo-tokens/design.md
(D1), and the `repo-tokens` spec.

The rules:

  - Only `https://` URLs get a token. Plain HTTP would send it in the clear, and
    SSH has its own keys.
  - The Settings token for the URL's host: GitHub for `github.com` and
    `api.github.com`, gitlab.com for `gitlab.com`. Any other host gets none, and a
    forum there uses the system git's credentials (an SSH key or a credential
    helper). A project holds no token of its own.
"""

import logging
import os
from dataclasses import dataclass
from typing import Literal
from urllib import parse

from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import LocalSettingsTable

logger = logging.getLogger(__name__)

TokenSource = Literal["settings-github", "settings-gitlab"]
"""Where a token came from, which a failed publish names to the user (design D7)."""


@dataclass(frozen=True)
class RepoToken:
    value: str
    source: TokenSource


def repo_token(url: str, local: LocalSettingsTable) -> RepoToken | None:
    """The token to send to url, or None to send none"""
    url_parts = parse.urlsplit(url)

    # only https gets a token
    if url_parts.scheme != "https":
        return None

    if (
        "github.com" == url_parts.hostname or "api.github.com" == url_parts.hostname
    ) and local.github_token:
        return RepoToken(local.github_token, "settings-github")
    elif "gitlab.com" == url_parts.hostname and local.gitlab_token:
        return RepoToken(local.gitlab_token, "settings-gitlab")

    return None


async def settings_repo_token(session: AsyncSession, url: str) -> RepoToken | None:
    """
    `repo_token` for a caller that does not hold the settings row.

    Reads the row now rather than taking the settings an agent was built from:
    the tokens are not on that model, and a token changed in Settings applies to
    the next call without evicting anything (design D2).
    """
    from . import local_settings

    return repo_token(url, await local_settings.get(session))


LEGACY_TOKEN_ENV = ("VISTA_BACKEND_GITHUB_TOKEN", "VISTA_BACKEND_GITLAB_TOKEN")


def check_legacy_token_env() -> None:
    """
    Say out loud that the old token variables do nothing (design D5).

    Settings ignores unknown variables, so a leftover `.env` line would otherwise
    look like it still worked while private imports quietly failed. `.env` is
    loaded into the environment at import, so this reads both. Names only: the
    value is never logged.
    """
    for name in LEGACY_TOKEN_ENV:
        if os.environ.get(name):
            logger.warning(
                "%s no longer does anything — repository tokens are set in "
                "Settings → Repositories. Add the token there and delete this "
                "line.",
                name,
            )
