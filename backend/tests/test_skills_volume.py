"""
The staged skills volume must be readable from inside the sandbox, whatever user the
sandbox runs as, without shelling out to POSIX-only tools such as `chmod`.
"""

import asyncio
import os
import stat
from pathlib import Path

import pytest

from vista_backend.agents.agents import _make_readable_by_others, _reset_dir

pytestmark = [pytest.mark.unit]


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_grants_other_read_and_traverse(tmp_path: Path):
    root = tmp_path / "skills"
    nested = root / "salt" / "scripts"
    nested.mkdir(parents=True)
    doc = root / "salt" / "SKILL.md"
    doc.write_text("# salt\n", encoding="utf-8")
    script = nested / "run.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    for path, mode in [
        (doc, 0o600),
        (script, 0o700),
        (nested, 0o700),
        (root / "salt", 0o700),
    ]:
        path.chmod(mode)
    (root / "link").symlink_to(doc)

    _make_readable_by_others(root)

    assert _mode(doc) == 0o604
    assert _mode(script) == 0o705
    assert _mode(nested) & 0o005 == 0o005
    assert _mode(root / "salt") & 0o005 == 0o005
    assert _mode(root) & 0o005 == 0o005


def test_does_not_spawn_a_process(tmp_path: Path, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError(f"spawned {args}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    (tmp_path / "SKILL.md").write_text("x", encoding="utf-8")

    _make_readable_by_others(tmp_path)


def test_reset_dir_creates_and_empties(tmp_path: Path):
    root = tmp_path / "skills"
    _reset_dir(root)
    assert root.is_dir() and not any(root.iterdir())

    (root / "removed-skill").mkdir()
    (root / "removed-skill" / "SKILL.md").write_text("x", encoding="utf-8")
    _reset_dir(root)
    assert root.is_dir() and not any(root.iterdir())


def test_reset_dir_raises_when_cleanup_fails(tmp_path: Path, monkeypatch):
    """A skill left behind by a failed cleanup would stay mounted in the sandbox."""
    root = tmp_path / "skills"
    (root / "removed-skill").mkdir(parents=True)

    def fail(path, *args, **kwargs):
        raise PermissionError(f"in use: {path}")

    monkeypatch.setattr("shutil.rmtree", fail)
    with pytest.raises(PermissionError):
        _reset_dir(root)
