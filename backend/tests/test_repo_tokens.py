"""
Repository tokens: the researcher's GitHub and gitlab.com tokens, and which one a URL gets.

The tokens are write-only. The Settings window never has them to send back, so a
save that leaves them out, or sends null, has to keep them; only "" removes one.
And they stay out of `LocalSettingsPublic`, which is what an agent is built
from: `services/repo_tokens` reads them from the settings row when they are used.
"""

import pytest
from sqlalchemy import text

from vista_backend.agents.campaign.mcp_invoke import build_metadata
from vista_backend.api.settings import update_settings
from vista_backend.db.schemas import (
    LocalSettingsPublic,
    LocalSettingsTable,
    LocalSettingsUpdate,
)
from vista_backend.services import local_settings
from vista_backend.services.repo_tokens import check_legacy_token_env

GITHUB = "ghp_not_a_real_token"
GITLAB = "glpat-not-a-real-token"


async def _local(session) -> LocalSettingsPublic:
    return await local_settings.get_public(session)


async def _save(session, **fields):
    return await update_settings(
        LocalSettingsUpdate(**fields), session, await _local(session)
    )


# --------------------------------------------------------------------------- #
# 1.1 — Tokens in Settings
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_a_saved_token_is_only_ever_reported_as_saved(session):
    saved = await _save(session, github_token=GITHUB, gitlab_token=GITLAB)

    for shown in (saved, await _local(session)):
        dumped = shown.model_dump(mode="json")
        assert dumped["has_github_token"] is True
        assert dumped["has_gitlab_token"] is True
        assert GITHUB not in str(dumped) and GITLAB not in str(dumped)


@pytest.mark.anyio
async def test_the_tokens_never_reach_an_agent(session):
    """`LocalSettingsPublic` is what agents and MCP metadata are built from."""
    await _save(session, github_token=GITHUB)

    meta = build_metadata(await _local(session), {})
    assert GITHUB not in str(meta)


@pytest.mark.anyio
async def test_an_unrelated_save_keeps_the_tokens(session):
    await _save(session, github_token=GITHUB, gitlab_token=GITLAB)

    await _save(session, research_role="chemist")
    await _save(session, github_token=None)

    row = await local_settings.get(session)
    assert row.github_token == GITHUB
    assert row.gitlab_token == GITLAB


@pytest.mark.anyio
async def test_blank_removes_one_token_and_leaves_the_other(session):
    await _save(session, github_token=GITHUB, gitlab_token=GITLAB)

    saved = await _save(session, github_token="")

    assert saved.has_github_token is False
    assert saved.has_gitlab_token is True
    assert (await local_settings.get(session)).github_token is None


@pytest.mark.anyio
async def test_the_tokens_are_encrypted_at_rest(session):
    await _save(session, github_token=GITHUB, gitlab_token=GITLAB)

    stored = (
        await session.exec(
            text("SELECT github_token, gitlab_token FROM local_settings WHERE id = 1")
        )
    ).one()
    assert all(value and value not in (GITHUB, GITLAB) for value in stored)


# --------------------------------------------------------------------------- #
# 1.2 — Which token a URL gets
# --------------------------------------------------------------------------- #

BOTH = {"github_token": GITHUB, "gitlab_token": GITLAB}


@pytest.mark.parametrize(
    ("url", "tokens", "expected"),
    [
        # Imports, fetches and forums alike: the Settings token for the host.
        ("https://github.com/o/r", BOTH, (GITHUB, "settings-github")),
        (
            "https://api.github.com/repos/o/r/tarball",
            BOTH,
            (GITHUB, "settings-github"),
        ),
        ("https://GitHub.com/o/r", BOTH, (GITHUB, "settings-github")),
        ("https://gitlab.com/g/sub/r", BOTH, (GITLAB, "settings-gitlab")),
        (
            "https://gitlab.com/api/v4/projects/1",
            BOTH,
            (GITLAB, "settings-gitlab"),
        ),
        ("https://github.com/o/forum.git", BOTH, (GITHUB, "settings-github")),
        # Any other host gets none: a forum there uses the system git.
        ("https://code.example.org/g/r.git", BOTH, None),
        ("https://code.example.org/g/forum.git", BOTH, None),
        ("https://github.com/o/r", {"gitlab_token": GITLAB}, None),
        ("https://github.com.evil.example/o/r", BOTH, None),
        ("http://github.com/o/r", BOTH, None),
        ("git@github.com:o/forum.git", BOTH, None),
    ],
)
def test_which_token_a_url_gets(url, tokens, expected):
    from vista_backend.services.repo_tokens import repo_token

    row = LocalSettingsTable(**tokens)

    got = repo_token(url, row)

    assert (None if got is None else (got.value, got.source)) == expected


# --------------------------------------------------------------------------- #
# 5.1 — The environment variables are gone, out loud
# --------------------------------------------------------------------------- #


def test_a_leftover_token_variable_is_named_not_used(monkeypatch, caplog):
    monkeypatch.setenv("VISTA_BACKEND_GITHUB_TOKEN", GITHUB)
    monkeypatch.delenv("VISTA_BACKEND_GITLAB_TOKEN", raising=False)
    with caplog.at_level("WARNING"):
        check_legacy_token_env()

    assert "VISTA_BACKEND_GITHUB_TOKEN no longer does anything" in caplog.text
    assert "Settings → Repositories" in caplog.text
    assert "GITLAB" not in caplog.text, "only the variable that is set"
    assert GITHUB not in caplog.text, "never the value"


def test_no_variable_no_warning(monkeypatch, caplog):
    for name in ("VISTA_BACKEND_GITHUB_TOKEN", "VISTA_BACKEND_GITLAB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    with caplog.at_level("WARNING"):
        check_legacy_token_env()
    assert caplog.text == ""


def test_settings_no_longer_has_the_tokens():
    from vista_backend.config import Settings

    assert "github_token" not in Settings.model_fields
    assert "gitlab_token" not in Settings.model_fields
