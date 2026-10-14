"""
Which repository token a URL gets, and where it came from.

The one place that decides it, so skill import, `fetch_repository` and a project's
forum cannot drift apart. Each caller loads the user's row and passes it in; this
module only chooses. See openspec/changes/global-repo-tokens/design.md (D1).

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

from dataclasses import dataclass
from typing import Literal

from ..db.schemas import ProjectTable, UserTable

TokenSource = Literal["project", "settings-github", "settings-gitlab"]
"""Where a token came from, which a failed publish names to the user (design D7)."""


@dataclass(frozen=True)
class RepoToken:
    value: str
    source: TokenSource


def repo_token(
    url: str, user: UserTable, project: ProjectTable | None = None
) -> RepoToken | None:
    """The token to send to `url`, or None to send none."""
    raise NotImplementedError("task 1.2 — Sam's carve-out")
