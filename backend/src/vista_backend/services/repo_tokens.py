"""
Which repository token a URL gets, and where it came from.

The one place that decides it, so skill import, `fetch_repository` and a project's
forum cannot drift apart. Each caller loads the user's row and passes it in; this
module only chooses. See openspec/changes/archive/2026-10-15-global-repo-tokens/design.md
(D1), and the `repo-tokens` spec.

The rules:

  - Only `https://` URLs get a token. Plain HTTP would send it in the clear, and
    SSH has its own keys.
  - With a project (only the forum passes one), the project's own token wins, for
    whatever `https://` host its forum is on.
  - Otherwise the user's Settings token for the URL's host: GitHub for
    `github.com` and `api.github.com`, gitlab.com for `gitlab.com`. Any other host
    gets none.
  - Without a project, a project's token is never returned: it is usually scoped
    to the forum repository alone.
"""

import logging
import os
import uuid
from dataclasses import dataclass
from typing import Literal
from urllib import parse

from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.schemas import ProjectTable, UserTable

logger = logging.getLogger(__name__)

TokenSource = Literal["project", "settings-github", "settings-gitlab"]
"""Where a token came from, which a failed publish names to the user (design D7)."""


@dataclass(frozen=True)
class RepoToken:
    value: str
    source: TokenSource


def repo_token(
    url: str, user: UserTable, project: ProjectTable | None = None
) -> RepoToken | None:
    """The token to send to url, or None to send none"""
    url_parts = parse.urlsplit(url)

    # only https gets a token
    if url_parts.scheme != "https":
        return None

    # if a project token exists, return it
    if project is not None and project.forum_repo_token:
        return RepoToken(project.forum_repo_token, "project")

    if (
        "github.com" == url_parts.hostname or "api.github.com" == url_parts.hostname
    ) and user.github_token:
        return RepoToken(user.github_token, "settings-github")
    elif "gitlab.com" == url_parts.hostname and user.gitlab_token:
        return RepoToken(user.gitlab_token, "settings-gitlab")

    return None


async def user_repo_token(
    session: AsyncSession, url: str, user_id: uuid.UUID
) -> RepoToken | None:
    """
    `repo_token` for a caller that has the user's id, not their row.

    Reads the row now rather than taking the user an agent was built from: the
    tokens are not on that model, and a token changed in Settings applies to the
    next call without evicting anything (design D2). Never with a project — only
    the forum passes one, and it already holds the project's row.
    """
    user = await session.get(UserTable, user_id)
    return repo_token(url, user) if user is not None else None


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
                "%s no longer does anything — repository tokens are set per "
                "user in Settings → Repositories. Add the token there and "
                "delete this line.",
                name,
            )
