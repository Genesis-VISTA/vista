"""
No Hypothesis Lab on a hosted VISTA (decided 2026-11-25): its debates run in the
background, where there is no sign-in to post with. A server service token for
hosted forums is a follow-up.
"""

import pytest

from vista_backend.agents.forum import project_forum
from vista_backend.config import settings
from vista_backend.db.schemas import ProjectTable
from vista_backend.services.hosted import FORUM_OFF_WHEN_HOSTED, forum_enabled


@pytest.fixture
def forum_on(monkeypatch):
    monkeypatch.setattr(settings.forum, "enabled", True)
    monkeypatch.setattr(
        project_forum,
        "git_status",
        lambda: type("G", (), {"ok": True, "reason": None})(),
    )


def _project() -> ProjectTable:
    return ProjectTable(name="lab", forum_repo_url="https://forge.test/lab.git")


def test_hosted_has_no_lab_and_says_why(forum_on, monkeypatch):
    monkeypatch.setattr(settings, "deployment", "hosted")
    assert not forum_enabled()
    assert not project_forum.lab_enabled(_project())
    with pytest.raises(project_forum.ForumDisabled, match="not available on a hosted"):
        project_forum.build_client_for(_project(), None)  # type: ignore[arg-type]
    assert "background" in FORUM_OFF_WHEN_HOSTED


def test_desktop_keeps_its_lab(forum_on, monkeypatch):
    monkeypatch.setattr(settings, "deployment", "desktop")
    assert forum_enabled()
    assert project_forum.lab_enabled(_project())
