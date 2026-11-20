"""Which volumes the dev server mounts (openspec/changes/linked-folders, task 2.1)."""

import json
from contextlib import asynccontextmanager
from pathlib import PurePosixPath

import pytest

import dev_mcp_server.server as server
from dev_mcp_server.config import AppSettings


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _FakeSandbox:
    async def close(self):
        pass


@pytest.fixture
def spawned(monkeypatch):
    calls: list[list] = []

    async def spawn(volumes=None, env=None):
        calls.append(list(volumes or []))
        return _FakeSandbox()

    monkeypatch.setitem(server.SANDBOX_SPAWN_FUNCS, server.settings.sandbox_mode, spawn)
    return calls


def _settings(monkeypatch, volumes) -> AppSettings:
    monkeypatch.setenv("VISTA_DEV_MCP_VOLUMES", json.dumps(volumes))
    s = AppSettings()
    monkeypatch.setattr(server.settings, "volumes", s.volumes)
    return s


async def _start():
    async with asynccontextmanager(server.app_lifespan._fn)(None):
        pass


@pytest.mark.anyio
async def test_owned_volume_is_created(tmp_path, monkeypatch, spawned):
    project = tmp_path / "volumes" / "p1"
    _settings(monkeypatch, [[str(project), "/mnt", "w"]])
    await _start()
    assert project.is_dir()
    assert spawned == [[(project, PurePosixPath("/mnt"), "w")]]


@pytest.mark.anyio
async def test_owned_flag_true_is_created(tmp_path, monkeypatch, spawned):
    skills = tmp_path / "skills"
    _settings(monkeypatch, [[str(skills), "/mnt/skills", "r", True]])
    await _start()
    assert skills.is_dir()
    assert spawned == [[(skills, PurePosixPath("/mnt/skills"), "r")]]


@pytest.mark.anyio
async def test_missing_unowned_volume_is_neither_created_nor_mounted(
    tmp_path, monkeypatch, spawned
):
    project = tmp_path / "project"
    missing = tmp_path / "External" / "msr-runs"
    _settings(
        monkeypatch,
        [
            [str(project), "/mnt", "w"],
            [str(missing), "/mnt/linked/msr-runs", "r", False],
        ],
    )
    await _start()
    assert not missing.exists() and not missing.parent.exists()
    assert spawned == [[(project, PurePosixPath("/mnt"), "w")]]


@pytest.mark.anyio
async def test_unowned_file_is_not_mounted(tmp_path, monkeypatch, spawned):
    a_file = tmp_path / "a.txt"
    a_file.write_text("x", encoding="utf-8")
    _settings(monkeypatch, [[str(a_file), "/mnt/linked/a", "r", False]])
    await _start()
    assert spawned == [[]]


@pytest.mark.anyio
async def test_unowned_volume_through_a_symlink_reaches_spawn_resolved(
    tmp_path, monkeypatch, spawned
):
    real = tmp_path / "real" / "salt-data"
    real.mkdir(parents=True)
    (tmp_path / "via").symlink_to(tmp_path / "real")
    _settings(
        monkeypatch,
        [[str(tmp_path / "via" / "salt-data"), "/mnt/linked/s", "r", False]],
    )
    await _start()
    assert spawned == [[(real.resolve(), PurePosixPath("/mnt/linked/s"), "r")]]
