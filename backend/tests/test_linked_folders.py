"""Linked folders in the sandbox (openspec change linked-folders, tasks 2.2 and 2.3)."""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from vista_backend.config import settings
from vista_backend.services import linked_folders
from vista_backend.services.linked_folders import (
    clear_leftover_mount_points,
    resolve_linked_volumes,
)


@dataclass
class Link:
    name: str
    host_path: str


def test_real_and_missing_link(tmp_path):
    real = tmp_path / "salt-data"
    real.mkdir()
    missing = tmp_path / "External" / "msr-runs"

    out = resolve_linked_volumes(
        [Link("salt-data", str(real)), Link("msr-runs", str(missing))]
    )

    assert out.available == [(str(real.resolve()), "/mnt/linked/salt-data", "r", False)]
    assert out.unavailable == [("msr-runs", "missing")]
    assert not missing.exists()


def test_a_file_is_not_a_folder(tmp_path):
    a_file = tmp_path / "a.txt"
    a_file.write_text("x", encoding="utf-8")
    out = resolve_linked_volumes([Link("a", str(a_file))])
    assert out.available == [] and out.unavailable == [("a", "not a folder")]


def test_unreadable_folder(tmp_path, monkeypatch):
    real = tmp_path / "locked"
    real.mkdir()
    monkeypatch.setattr(linked_folders.os, "access", lambda *_: False)
    out = resolve_linked_volumes([Link("locked", str(real))])
    assert out.unavailable == [("locked", "unreadable")]


def test_symlink_loop_is_not_mounted(tmp_path):
    (tmp_path / "loop").symlink_to(tmp_path / "loop")
    out = resolve_linked_volumes([Link("loop", str(tmp_path / "loop"))])
    assert out.available == [] and out.unavailable == [("loop", "missing")]


def test_link_stored_through_a_symlink_is_mounted_resolved(tmp_path):
    real = tmp_path / "real" / "salt-data"
    real.mkdir(parents=True)
    (tmp_path / "via").symlink_to(tmp_path / "real")
    out = resolve_linked_volumes(
        [Link("salt-data", str(tmp_path / "via" / "salt-data"))]
    )
    assert out.available == [(str(real.resolve()), "/mnt/linked/salt-data", "r", False)]


def test_no_links():
    out = resolve_linked_volumes([])
    assert out.available == [] and out.unavailable == []


def _dev_volumes(agent) -> list:
    [sandbox] = [
        s
        for s in agent._mcp_servers  # noqa: SLF001
        if "VISTA_DEV_MCP_VOLUMES" in (getattr(s, "env", None) or {})
    ]
    return json.loads(sandbox.env["VISTA_DEV_MCP_VOLUMES"])


def test_project_agent_mounts_available_links_read_only(tmp_path, monkeypatch):
    from harness import make_local_settings, make_project

    from vista_backend.agents.agents import ProjectAgent

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    real = tmp_path / "salt-data"
    real.mkdir()
    agent = ProjectAgent(
        make_project(),
        make_local_settings(),
        linked_folders=[
            Link("salt-data", str(real)),
            Link("msr-runs", str(tmp_path / "gone")),
        ],
    )
    volumes = _dev_volumes(agent)
    assert [str(agent.volume_root), "/mnt", "w"] in volumes
    assert [str(real.resolve()), "/mnt/linked/salt-data", "r", False] in volumes
    assert not any("msr-runs" in v[1] for v in volumes)
    assert agent.linked.unavailable == [("msr-runs", "missing")]


def test_project_agent_without_links_mounts_as_before(tmp_path, monkeypatch):
    from harness import make_local_settings, make_project

    from vista_backend.agents.agents import ProjectAgent

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    agent = ProjectAgent(make_project(), make_local_settings())
    assert _dev_volumes(agent) == [
        [str(agent.volume_root), "/mnt", "w"],
        [str(agent.skills_volume_dir), "/mnt/skills", "r"],
    ]


def _leftovers(volume_root: Path) -> Path:
    root = volume_root / "linked"
    (root / "old").mkdir(parents=True)
    (root / "kept").mkdir()
    (root / "kept" / "note.txt").write_text("written by the agent", encoding="utf-8")
    (root / "salt-data").mkdir()
    return root


def test_leftover_mount_points_are_cleared(tmp_path):
    root = _leftovers(tmp_path)
    clear_leftover_mount_points(tmp_path, keep={"salt-data"})
    assert sorted(p.name for p in root.iterdir()) == ["kept", "salt-data"]
    assert (root / "kept" / "note.txt").exists()


def test_leftover_symlink_is_left_alone(tmp_path):
    root = tmp_path / "linked"
    root.mkdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    (root / "sneaky").symlink_to(target)
    clear_leftover_mount_points(tmp_path, keep=set())
    assert (root / "sneaky").is_symlink() and target.is_dir()


def test_no_linked_folder_yet(tmp_path):
    clear_leftover_mount_points(tmp_path, keep=set())
    assert not (tmp_path / "linked").exists()


def test_refused_rmdir_is_skipped(tmp_path, monkeypatch):
    root = _leftovers(tmp_path)

    def refuse(self):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "rmdir", refuse)
    clear_leftover_mount_points(tmp_path, keep={"salt-data"})
    assert (root / "old").is_dir()


@pytest.mark.anyio
async def test_setup_volumes_clears_leftovers_and_survives_a_refusal(
    tmp_path, monkeypatch, engine
):
    from harness import make_local_settings, make_project

    from vista_backend.agents.agents import ProjectAgent

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    real = tmp_path / "salt-data"
    real.mkdir()
    agent = ProjectAgent(
        make_project(),
        make_local_settings(),
        linked_folders=[Link("salt-data", str(real))],
    )
    root = _leftovers(agent.volume_root)
    monkeypatch.setattr("vista_backend.agents.agents.get_engine", lambda: engine)

    await agent._setup_volumes()  # noqa: SLF001
    assert sorted(p.name for p in root.iterdir()) == ["kept", "salt-data"]

    (root / "old").mkdir()

    def refuse(self):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "rmdir", refuse)
    await agent._setup_volumes()  # noqa: SLF001
    assert (root / "old").is_dir()


def test_display_file_can_reach_each_mounted_link(tmp_path, monkeypatch):
    """openspec change linked-folders, task 5.3: a `uri_map` entry per mounted link."""
    from harness import make_local_settings, make_project

    from vista_backend.agents.agents import ProjectAgent

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    real = tmp_path / "salt-data"
    real.mkdir()
    agent = ProjectAgent(
        make_project(name="molten-salt"),
        make_local_settings(),
        linked_folders=[
            Link("salt-data", str(real)),
            Link("msr-runs", str(tmp_path / "gone")),
        ],
    )
    uri_map = agent._build_vista_metadata("display_file")["vista"]["uri_map"]  # noqa: SLF001
    assert uri_map["file:///mnt/linked/salt-data/{path}"] == (
        "/api/files/linked/salt-data/{path}?project_name=molten-salt"
    )
    assert not any("msr-runs" in key for key in uri_map)
