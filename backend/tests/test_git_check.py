"""
The Hypothesis Lab's git prerequisite (openspec/changes/forum-git-backend, D9).

Each case stands a stub `git` or `xcode-select` on a private `PATH`, so what is
tested is the check's reading of what it finds, not whatever this machine has.
One test at the end runs the real git, because a check that only ever passed
against stubs could be parsing a version string no git prints.
"""

import shutil
import stat
import sys
from pathlib import Path

import pytest

from vista_backend.services import git_check
from vista_backend.services.git_check import (
    MACOS_HINT,
    NOT_INSTALLED,
    check_git,
    git_status,
    reset_git_status,
)


def _stub(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def bin_dir(tmp_path, monkeypatch) -> Path:
    """An empty directory that is the whole of `PATH`."""
    d = tmp_path / "bin"
    d.mkdir()
    monkeypatch.setenv("PATH", str(d))
    reset_git_status()
    yield d
    reset_git_status()


def test_no_git_on_path_is_not_installed(bin_dir):
    result = check_git("git")
    assert not result.ok
    assert result.reason == NOT_INSTALLED
    assert result.path is None


def test_a_recent_git_is_accepted(bin_dir):
    _stub(bin_dir / "git", 'echo "git version 2.39.5 (Apple Git-154)"')
    result = check_git("git")
    assert result.ok, result.reason
    assert result.version == (2, 39)
    assert result.path == str(bin_dir / "git")
    assert result.reason is None


def test_the_minimum_version_is_accepted(bin_dir):
    _stub(bin_dir / "git", 'echo "git version 2.34.0"')
    assert check_git("git").ok


def test_an_old_git_names_both_versions(bin_dir):
    _stub(bin_dir / "git", 'echo "git version 2.25.1"')
    result = check_git("git")
    assert not result.ok
    assert result.version == (2, 25)
    assert (
        result.reason == "Git 2.25 is too old; the Hypothesis Lab needs 2.34 or later."
    )


def test_a_git_that_fails_reports_what_it_said(bin_dir):
    _stub(bin_dir / "git", 'echo "fatal: cannot load libcurl" >&2; exit 1')
    result = check_git("git")
    assert not result.ok
    assert "fatal: cannot load libcurl" in result.reason


def test_an_absolute_git_binary_is_used_as_given(tmp_path, bin_dir):
    elsewhere = _stub(tmp_path / "opt" / "git", 'echo "git version 2.45.1"')
    result = check_git(str(elsewhere))
    assert result.ok
    assert result.path == str(elsewhere)


class TestMacosShim:
    """`/usr/bin/git` without the developer tools is absent, and is never run."""

    @pytest.fixture
    def shim(self, tmp_path, bin_dir, monkeypatch) -> Path:
        # A stand-in for /usr/bin/git that leaves evidence if anything runs it.
        ran = tmp_path / "shim-ran"
        shim = _stub(
            bin_dir / "git", f': > "{ran}"; echo "git version 2.39.5 (Apple Git-154)"'
        )
        monkeypatch.setattr(git_check, "MACOS_GIT_SHIM", shim)
        monkeypatch.setattr(sys, "platform", "darwin")
        return ran

    def test_without_developer_tools_the_shim_is_never_run(self, bin_dir, shim):
        _stub(bin_dir / "xcode-select", "exit 2")
        result = check_git("git")
        assert not result.ok
        assert result.reason == NOT_INSTALLED + MACOS_HINT
        assert not shim.exists(), "running the shim pops the install dialog"

    def test_without_xcode_select_the_shim_is_never_run(self, bin_dir, shim):
        result = check_git("git")
        assert not result.ok
        assert not shim.exists()

    def test_with_developer_tools_the_shim_is_real_git(self, bin_dir, shim):
        _stub(bin_dir / "xcode-select", 'echo "/Library/Developer/CommandLineTools"')
        result = check_git("git")
        assert result.ok, result.reason
        assert shim.exists()

    def test_another_git_on_macos_skips_xcode_select(self, tmp_path, bin_dir, shim):
        asked = tmp_path / "asked"
        _stub(bin_dir / "xcode-select", f': > "{asked}"; exit 2')
        brew = _stub(tmp_path / "brew" / "git", 'echo "git version 2.51.0"')
        assert check_git(str(brew)).ok
        assert not asked.exists()


def test_off_macos_the_shim_path_is_not_special(tmp_path, bin_dir, monkeypatch):
    shim = _stub(bin_dir / "git", 'echo "git version 2.43.0"')
    _stub(bin_dir / "xcode-select", "exit 2")
    monkeypatch.setattr(git_check, "MACOS_GIT_SHIM", shim)
    monkeypatch.setattr(sys, "platform", "linux")
    assert check_git("git").ok


def test_a_missing_git_is_rechecked_so_installing_it_turns_the_lab_on(bin_dir):
    assert not git_status("git").ok
    _stub(bin_dir / "git", 'echo "git version 2.47.0"')
    assert git_status("git").ok


def test_a_found_git_is_cached(tmp_path, bin_dir):
    calls = tmp_path / "calls"
    _stub(bin_dir / "git", f'echo x >> "{calls}"; echo "git version 2.47.0"')
    assert git_status("git").ok
    assert git_status("git").ok
    assert calls.read_text(encoding="utf-8").count("x") == 1


def test_git_status_defaults_to_the_configured_binary(tmp_path, bin_dir, monkeypatch):
    from vista_backend.config import settings

    custom = _stub(tmp_path / "custom" / "git", 'echo "git version 2.40.0"')
    monkeypatch.setattr(
        settings, "forum", settings.forum.model_copy(update={"git_binary": str(custom)})
    )
    assert git_status().path == str(custom)


@pytest.mark.skipif(shutil.which("git") is None, reason="no git on this host")
def test_this_hosts_real_git_is_readable():
    reset_git_status()
    result = check_git("git")
    assert result.version is not None, result.reason
