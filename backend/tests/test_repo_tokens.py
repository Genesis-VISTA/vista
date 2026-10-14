"""
Repository tokens: the researcher's GitHub and gitlab.com tokens, and which one a URL gets.

The tokens are write-only. The Settings window never has them to send back, so a
save that leaves them out, or sends null, has to keep them; only "" removes one.
And they stay out of `UserPublicWithConfig`, which is what an agent is built
from: `services/repo_tokens` reads them from the user's row when they are used.
"""

import pytest
from sqlalchemy import text

from vista_backend.agents.campaign.mcp_invoke import build_metadata
from vista_backend.api.users import get_me, update_me
from vista_backend.db.schemas import UserPublicWithConfig, UserSelfUpdate, UserTable

GITHUB = "ghp_not_a_real_token"
GITLAB = "glpat-not-a-real-token"


async def _save(session, row, **fields):
    return await update_me(UserSelfUpdate(**fields), session, row)


# --------------------------------------------------------------------------- #
# 1.1 — Tokens on the user
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_saved_token_is_only_ever_reported_as_saved(session, alice):
    row = await session.get(UserTable, alice.id)
    saved = await _save(session, row, github_token=GITHUB, gitlab_token=GITLAB)

    for shown in (saved, await get_me(row, config=True)):
        dumped = shown.model_dump(mode="json")
        assert dumped["has_github_token"] is True
        assert dumped["has_gitlab_token"] is True
        assert GITHUB not in str(dumped) and GITLAB not in str(dumped)


@pytest.mark.anyio
async def test_the_tokens_never_reach_an_agent(session, alice):
    """`UserPublicWithConfig` is what agents and MCP metadata are built from."""
    row = await session.get(UserTable, alice.id)
    await _save(session, row, github_token=GITHUB)

    meta = build_metadata(UserPublicWithConfig.model_validate(row), {})
    assert GITHUB not in str(meta)


@pytest.mark.anyio
async def test_an_unrelated_save_keeps_the_tokens(session, alice):
    row = await session.get(UserTable, alice.id)
    await _save(session, row, github_token=GITHUB, gitlab_token=GITLAB)

    await _save(session, row, research_role="chemist")
    await _save(session, row, github_token=None)

    assert row.github_token == GITHUB
    assert row.gitlab_token == GITLAB


@pytest.mark.anyio
async def test_blank_removes_one_token_and_leaves_the_other(session, alice):
    row = await session.get(UserTable, alice.id)
    await _save(session, row, github_token=GITHUB, gitlab_token=GITLAB)

    saved = await _save(session, row, github_token="")

    assert saved.has_github_token is False
    assert saved.has_gitlab_token is True
    assert row.github_token is None


@pytest.mark.anyio
async def test_the_tokens_are_encrypted_at_rest(session, alice):
    row = await session.get(UserTable, alice.id)
    await _save(session, row, github_token=GITHUB, gitlab_token=GITLAB)

    stored = (
        await session.exec(
            text("SELECT github_token, gitlab_token FROM app_user WHERE id = :id"),
            params={"id": alice.id.hex},
        )
    ).one()
    assert all(value and value not in (GITHUB, GITLAB) for value in stored)


# --------------------------------------------------------------------------- #
# 1.2 — Which token a URL gets
# --------------------------------------------------------------------------- #

PROJECT = "github_pat_project_scoped"

BOTH = {"github_token": GITHUB, "gitlab_token": GITLAB}


@pytest.mark.parametrize(
    ("url", "user_tokens", "project_token", "expected"),
    [
        # Imports and fetches: no project, so the user's token for the host.
        ("https://github.com/o/r", BOTH, None, (GITHUB, "settings-github")),
        (
            "https://api.github.com/repos/o/r/tarball",
            BOTH,
            None,
            (GITHUB, "settings-github"),
        ),
        ("https://GitHub.com/o/r", BOTH, None, (GITHUB, "settings-github")),
        ("https://gitlab.com/g/sub/r", BOTH, None, (GITLAB, "settings-gitlab")),
        (
            "https://gitlab.com/api/v4/projects/1",
            BOTH,
            None,
            (GITLAB, "settings-gitlab"),
        ),
        ("https://code.example.org/g/r.git", BOTH, None, None),
        ("https://github.com/o/r", {"gitlab_token": GITLAB}, None, None),
        ("https://github.com.evil.example/o/r", BOTH, None, None),
        ("http://github.com/o/r", BOTH, None, None),
        # The forum: its project's token first, then the user's for the host.
        ("https://github.com/o/forum.git", BOTH, PROJECT, (PROJECT, "project")),
        ("https://github.com/o/forum.git", BOTH, "", (GITHUB, "settings-github")),
        ("https://code.example.org/g/forum.git", BOTH, PROJECT, (PROJECT, "project")),
        ("https://code.example.org/g/forum.git", BOTH, "", None),
        ("http://github.com/o/forum.git", BOTH, PROJECT, None),
        ("git@github.com:o/forum.git", BOTH, PROJECT, None),
    ],
)
def test_which_token_a_url_gets(url, user_tokens, project_token, expected):
    from vista_backend.db.schemas import ProjectTable
    from vista_backend.services.repo_tokens import repo_token

    user = UserTable(email="r@example.org", **user_tokens)
    project = (
        None
        if project_token is None
        else ProjectTable(
            name="p", forum_repo_url=url, forum_repo_token=project_token or None
        )
    )

    got = repo_token(url, user, project)

    assert (None if got is None else (got.value, got.source)) == expected


def test_without_a_project_its_token_is_never_returned():
    """There is no way to pass one in — the signature is the guarantee."""
    from vista_backend.services.repo_tokens import repo_token

    user = UserTable(email="r@example.org")
    assert repo_token("https://github.com/o/r", user) is None
