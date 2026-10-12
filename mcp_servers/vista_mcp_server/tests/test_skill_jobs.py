"""HPC jobs shipped inside a skill (`<skill>/hpc_jobs/<job>/`, submitted as `<skill>/<job>`).

The project's skills volume holds exactly the skills it loads, and the backend
sends its path on every call. So the lookup is per call, against that folder,
and a skill the project has not loaded provides nothing. Everything here runs
against a temporary skills folder and the fake SSH connection.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp.exceptions import ToolError

import vista_mcp_server.submit_job_mcp as m
from vista_mcp_server.config import settings
from vista_mcp_server.lib.user_config import UserConfig
from fakes import FakeGlobusClient, FakeIriClient, FakeSshConn

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

BASE = "/lustre/orion/abc123/proj-shared/vista"
CFG = UserConfig(lux_remote_dir=BASE, lux_account="abc123")


def _write_skill_job(
    skills: Path, skill: str = "primus-on-lux", job: str = "primus-smoke"
) -> Path:
    d = skills / skill / "hpc_jobs" / job
    (d / "configs").mkdir(parents=True)
    (skills / skill / "SKILL.md").write_text(
        f"---\nname: {skill}\n---\n", encoding="utf-8"
    )
    (d / "README.md").write_text(f"# {job}\n\nA skill's job.\n", encoding="utf-8")
    (d / "cluster_defaults.json").write_text(
        json.dumps(
            {
                "lux": {
                    "duration": 600,
                    "resources": {"node_count": 1},
                    "iri": {"partition": "batch"},
                }
            }
        ),
        encoding="utf-8",
    )
    (d / "job.lux.slurm").write_text('echo "run $@"\n', encoding="utf-8")
    (d / "setup_lux.sh").write_text("echo setup\n", encoding="utf-8")
    (d / "summarize.sh").write_text("echo sum\n", encoding="utf-8")
    (d / "configs" / "model.yaml").write_text("layers: 2\n", encoding="utf-8")
    (d / "configs" / "README.md").write_text("configs\n", encoding="utf-8")
    return d


@pytest.fixture
def skills(tmp_path) -> Path:
    s = tmp_path / "volume" / "skills"
    _write_skill_job(s)
    return s


def _ctx(skills_dir: Path | None, user: UserConfig = CFG):
    vista = {
        "user": user.model_dump(),
        "project_paths": {"skills_dir": str(skills_dir) if skills_dir else None},
    }
    return SimpleNamespace(
        request_context=SimpleNamespace(meta=SimpleNamespace(vista=vista))
    )


# --- the resolver -----------------------------------------------------------


def test_a_loaded_skills_job_resolves_to_the_skills_folder(skills):
    info = m.resolve_job("primus-on-lux/primus-smoke", skills)
    assert info.name == "primus-on-lux/primus-smoke"
    assert info.skill == "primus-on-lux"
    assert (
        info.job_dir
        == (skills / "primus-on-lux" / "hpc_jobs" / "primus-smoke").resolve()
    )
    assert info.cluster_defaults.lux is not None
    assert info.description.startswith("# primus-smoke")


def test_built_in_jobs_still_come_from_vistas_catalog(skills):
    info = m.resolve_job("example", skills)
    assert info.skill is None
    assert info.job_dir == settings.local_hpc_jobs_dir / "example"


def test_a_skill_the_project_does_not_load_provides_nothing(tmp_path):
    with pytest.raises(ToolError, match="not recognized"):
        m.resolve_job("primus-on-lux/primus-smoke", tmp_path / "empty")
    with pytest.raises(ToolError, match="not recognized"):
        m.resolve_job("primus-on-lux/primus-smoke", None)


@pytest.mark.parametrize(
    "name",
    [
        "../x/primus-smoke",
        "primus-on-lux/..",
        "a/b/c",
        "primus-on-lux/.hidden",
        "/etc/passwd",
        "a//b",
    ],
)
def test_path_tricks_are_refused(skills, name):
    with pytest.raises(ToolError):
        m.resolve_job(name, skills)


def test_a_skill_symlinked_out_of_the_volume_is_refused(skills, tmp_path):
    outside = tmp_path / "elsewhere"
    _write_skill_job(outside, skill="real")
    (skills / "linked").symlink_to(outside / "real")
    with pytest.raises(ToolError, match="outside"):
        m.resolve_job("linked/primus-smoke", skills)


@pytest.mark.parametrize(
    "meta", ["job.lux.slurm", "setup_lux.sh", "README.md", "cluster_defaults.json"]
)
def test_a_symlinked_job_file_is_refused(skills, tmp_path, meta):
    """VISTA reads these itself and sends the scripts to the cluster, so a
    symlink to a file on this machine must never be followed."""
    d = skills / "primus-on-lux" / "hpc_jobs" / "primus-smoke"
    secret = tmp_path / "secret"
    secret.write_bytes((d / meta).read_bytes())
    (d / meta).unlink()
    (d / meta).symlink_to(secret)
    with pytest.raises(ToolError, match=f"{meta} must be a regular file"):
        m.resolve_job("primus-on-lux/primus-smoke", skills)


def test_a_broken_skill_job_names_itself_and_the_rule(skills):
    d = _write_skill_job(skills, skill="myskill", job="myjob")
    (d / "README.md").write_text("# other\n", encoding="utf-8")
    with pytest.raises(ToolError, match=r"myskill/myjob.*should start with"):
        m.resolve_job("myskill/myjob", skills)
    # ...and breaks nothing else.
    assert m.resolve_job("primus-on-lux/primus-smoke", skills).skill == "primus-on-lux"


def test_a_skill_job_without_a_script_is_refused(skills):
    d = _write_skill_job(skills, skill="myskill", job="myjob")
    (d / "job.lux.slurm").unlink()
    with pytest.raises(ToolError, match="no job script"):
        m.resolve_job("myskill/myjob", skills)


def test_an_unknown_job_lists_what_the_skills_provide(skills):
    with pytest.raises(ToolError) as e:
        m.resolve_job("primus-smoke", skills)
    assert "primus-on-lux/primus-smoke" in str(e.value)
    assert "example" in str(e.value)


def test_list_skill_jobs(skills):
    _write_skill_job(skills, skill="other", job="b")
    (skills / "plain").mkdir()
    assert m.list_skill_jobs(skills) == ["other/b", "primus-on-lux/primus-smoke"]
    assert m.list_skill_jobs(None) == []


async def test_the_tool_description_mentions_skill_jobs():
    [tool] = [t for t in await m.mcp.list_tools() if t.name == "submit_hpc_job"]
    assert "<skill>/<job>" in tool.description


# --- the upload set ---------------------------------------------------------


def test_sources_include_nested_files_and_skip_metadata_only_at_the_top(
    skills, tmp_path
):
    d = skills / "primus-on-lux" / "hpc_jobs" / "primus-smoke"
    (d / ".git").mkdir()
    (d / ".git" / "HEAD").write_text("x", encoding="utf-8")
    (d / "__pycache__").mkdir()
    (d / "__pycache__" / "a.pyc").write_bytes(b"x")
    secret = tmp_path / "secret.txt"
    secret.write_text("no", encoding="utf-8")
    (d / "configs" / "leak.txt").symlink_to(secret)
    assert [rel for rel, _ in m._job_sources(d)] == [
        "configs/README.md",
        "configs/model.yaml",
        "summarize.sh",
    ]


def test_source_subdirs_are_parents_first():
    sources = [("a/b/c.txt", Path("x")), ("a/d.txt", Path("x")), ("e.txt", Path("x"))]
    assert m._source_subdirs(sources) == ["a", "a/b"]


# --- end to end on Lux ------------------------------------------------------


@pytest.fixture
def lux(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "lux_proxy", None)
    monkeypatch.setattr(settings, "hpc_dry_run", False)
    conn = FakeSshConn(tmp_path / "remote")
    conn.on("sbatch --parsable", (0, "4242\n", ""))

    async def fake_lux_conn(ctx, tool, **call_args):
        return conn

    monkeypatch.setattr(m, "_lux_conn", fake_lux_conn)
    return conn


async def test_submit_hpc_job_runs_a_skill_job_on_lux(skills, lux):
    summary = await m.submit_hpc_job(
        _ctx(skills),
        job="primus-on-lux/primus-smoke",
        cluster="lux",
        script_args="TRAIN_ITERS=5",
    )
    src = f"{BASE}.researcher.jobs/primus-on-lux/primus-smoke/src"
    assert "job_id: 4242" in summary
    assert sorted(lux.puts) == sorted(
        f"{src}/{rel}"
        for rel in ("summarize.sh", "configs/model.yaml", "configs/README.md")
    )
    assert (
        lux.local(f"{src}/configs/model.yaml").read_text(encoding="utf-8")
        == "layers: 2\n"
    )
    [(_, script)] = lux.ran("sbatch --parsable")
    assert f"export RUN_DIR_Lux={src}" in script
    assert f"export VISTA_JOB_DIR={BASE}.out/primus-on-lux/primus-smoke" in script
    assert "set -- TRAIN_ITERS=5" in script
    assert lux.ran("echo setup"), "setup_lux.sh runs on the login node"

    # A second submission finds every file, nested ones too, and sends nothing.
    lux.puts.clear()
    await m.submit_hpc_job(
        _ctx(skills), job="primus-on-lux/primus-smoke", cluster="lux"
    )
    assert lux.puts == []


async def test_submit_hpc_job_refuses_a_skill_job_from_another_project(
    skills, lux, tmp_path
):
    with pytest.raises(ToolError, match="not recognized"):
        await m.submit_hpc_job(
            _ctx(tmp_path / "other-project"),
            job="primus-on-lux/primus-smoke",
            cluster="lux",
        )
    assert lux.commands == []


# --- nested uploads on the other transports ---------------------------------


async def test_globus_uploads_nested_files_and_makes_their_folders(skills):
    d = skills / "primus-on-lux" / "hpc_jobs" / "primus-smoke"
    globus = FakeGlobusClient()
    src = "/p/vista.u.jobs/primus-on-lux/primus-smoke/src"
    await m._sync_job_sources(globus, d, src, parents_below="/p", remote_endpoint="ep")
    assert {path for _, path in globus.uploads} == {
        f"{src}/summarize.sh",
        f"{src}/configs/model.yaml",
        f"{src}/configs/README.md",
    }
    assert ("ep", f"{src}/configs", src) in globus.mkdir_p_calls


async def test_globus_skips_a_complete_nested_tree(skills):
    d = skills / "primus-on-lux" / "hpc_jobs" / "primus-smoke"
    src = "/p/src"
    globus = FakeGlobusClient()
    globus.ls_entries[src] = [
        {
            "name": rel.rsplit("/", 1)[-1],
            "path": f"{src}/{rel}",
            "type": "file",
            "size": f.stat().st_size,
        }
        for rel, f in m._job_sources(d)
    ]
    await m._sync_job_sources(globus, d, src, parents_below="/p", remote_endpoint="ep")
    assert globus.uploads == []


async def test_perlmutter_uploads_nested_files(skills):
    d = skills / "primus-on-lux" / "hpc_jobs" / "primus-smoke"
    iri = FakeIriClient(job_id="1")
    await m._sync_perlmutter_sources(iri, d, "/r/jobs/x/src")
    assert iri.mkdirs == ["/r/jobs/x/src", "/r/jobs/x/src/configs"]
    assert {remote for _, remote in iri.uploads} == {
        "/r/jobs/x/src/summarize.sh",
        "/r/jobs/x/src/configs/model.yaml",
        "/r/jobs/x/src/configs/README.md",
    }
