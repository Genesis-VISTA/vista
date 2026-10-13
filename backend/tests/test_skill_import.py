"""
Skill import: the GitHub URL parser, importing an uploaded local folder, the
`POST /skills/import/upload` route that serves the Import modal's "Local
folder" tab, loading a new skill into the project it was made in, and
importing from the project's output folder ("From project output").
"""

import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from harness import api_client, seed_project, seed_user
from sqlmodel import select

from vista_backend.agents.agents import agent_volume_root, stage_skills

from vista_backend.agents import skill_import
from vista_backend.agents.skill_import import (
    SkillImportError,
    import_skill_from_files,
    parse_github_url,
)
from vista_backend.config import settings
from vista_backend.db.schemas import ProjectTable, SkillTable
from vista_backend.services.project import add_project_member

SKILL_MD = """---
name: demo-skill
description: A skill used by the import tests.
---

# Demo

Run `scripts/run.py`.
"""


def _files(entries: dict[str, str]) -> list[tuple[str, io.BytesIO]]:
    return [(path, io.BytesIO(text.encode("utf-8"))) for path, text in entries.items()]


class TestParseGithubUrl:
    def test_repo_root(self):
        parsed = parse_github_url("https://github.com/owner/repo.git")
        assert (parsed.owner, parsed.repo, parsed.ref, parsed.subpath) == (
            "owner",
            "repo",
            None,
            "",
        )

    def test_tree_subpath(self):
        parsed = parse_github_url(
            "https://github.com/anthropics/skills/tree/main/skills/docx/"
        )
        assert (parsed.ref, parsed.subpath) == ("main", "skills/docx")

    def test_rejects_local_path(self):
        with pytest.raises(SkillImportError, match="not a GitHub repo URL"):
            parse_github_url("/Users/me/skills/demo-skill")


class TestImportSkillFromFiles:
    def test_strips_picked_folder_name(self, tmp_path):
        dest = tmp_path / "out"
        skill = import_skill_from_files(
            _files(
                {
                    "demo-skill/SKILL.md": SKILL_MD,
                    "demo-skill/scripts/run.py": "print('hi')\n",
                }
            ),
            dest,
        )
        assert skill.name == "demo-skill"
        assert (dest / "SKILL.md").read_text(encoding="utf-8") == SKILL_MD
        assert (dest / "scripts" / "run.py").exists()

    def test_accepts_unprefixed_paths(self, tmp_path):
        skill = import_skill_from_files(
            _files({"SKILL.md": SKILL_MD}), tmp_path / "out"
        )
        assert skill.name == "demo-skill"

    def test_drops_picker_clutter(self, tmp_path):
        dest = tmp_path / "out"
        import_skill_from_files(
            _files(
                {
                    "demo-skill/SKILL.md": SKILL_MD,
                    "demo-skill/.DS_Store": "junk",
                    "demo-skill/.git/HEAD": "ref: refs/heads/main\n",
                    "demo-skill/scripts/__pycache__/run.cpython-312.pyc": "junk",
                }
            ),
            dest,
        )
        assert sorted(p.name for p in dest.rglob("*")) == ["SKILL.md"]

    def test_skill_md_must_be_at_root(self, tmp_path):
        with pytest.raises(SkillImportError, match="No SKILL.md"):
            import_skill_from_files(
                _files(
                    {"parent/demo-skill/SKILL.md": SKILL_MD, "parent/README.md": "x"}
                ),
                tmp_path / "out",
            )

    def test_invalid_skill_md(self, tmp_path):
        with pytest.raises(SkillImportError, match="invalid"):
            import_skill_from_files(
                _files({"demo/SKILL.md": "---\nname: demo\n---\nno description\n"}),
                tmp_path / "out",
            )

    @pytest.mark.parametrize(
        "bad",
        [
            "../escape.txt",
            "demo/../../escape.txt",
            "/etc/passwd",
            "demo\\evil.txt",
            "C:/evil.txt",
            "demo//SKILL.md",
            "",
        ],
    )
    def test_rejects_paths_that_escape(self, tmp_path, bad):
        with pytest.raises(SkillImportError, match="Invalid file path"):
            import_skill_from_files(
                _files({"demo/SKILL.md": SKILL_MD, bad: "x"}), tmp_path / "out"
            )
        assert not (tmp_path / "escape.txt").exists()
        assert not (tmp_path / "out").exists()

    def test_rejects_duplicates(self, tmp_path):
        files = _files({"demo/SKILL.md": SKILL_MD}) + _files(
            {"demo/SKILL.md": SKILL_MD}
        )
        with pytest.raises(SkillImportError, match="Duplicate"):
            import_skill_from_files(files, tmp_path / "out")

    def test_empty_upload(self, tmp_path):
        with pytest.raises(SkillImportError, match="No files"):
            import_skill_from_files(_files({"demo/.DS_Store": "x"}), tmp_path / "out")

    def test_size_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(skill_import, "MAX_UPLOAD_BYTES", 10)
        with pytest.raises(SkillImportError, match="larger than"):
            import_skill_from_files(
                _files({"demo/SKILL.md": SKILL_MD}), tmp_path / "out"
            )
        assert not (tmp_path / "out").exists()

    def test_file_count_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(skill_import, "MAX_UPLOAD_FILES", 1)
        with pytest.raises(SkillImportError, match="Too many files"):
            import_skill_from_files(
                _files({"demo/SKILL.md": SKILL_MD, "demo/a.txt": "a"}), tmp_path / "out"
            )


UPLOAD = "/skills/import/upload"


def _multipart(entries: dict[str, str]):
    files = [
        (
            "files",
            (path.rsplit("/", 1)[-1], text.encode("utf-8"), "application/octet-stream"),
        )
        for path, text in entries.items()
    ]
    data = {"paths": list(entries)}
    return files, data


@pytest.mark.anyio
class TestUploadRoute:
    @pytest.fixture(autouse=True)
    def _data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "data_dir", tmp_path)

    async def test_imports_private_skill(self, session, tmp_path):
        alice = await seed_user(session)
        files, data = _multipart(
            {"demo-skill/SKILL.md": SKILL_MD, "demo-skill/scripts/run.py": "print(1)\n"}
        )
        with api_client(session) as (client, _):
            response = await client.post(
                UPLOAD,
                files=files,
                data=data,
                headers={"X-Vista-User-Email": alice.email},
            )
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["name"] == "demo-skill"
            assert body["is_public"] is False
            assert body["repo_url"] is None
            assert "Run `scripts/run.py`." in body["body"]

            listed = await client.get(
                "/skills", headers={"X-Vista-User-Email": alice.email}
            )
        assert "demo-skill" in [s["name"] for s in listed.json()]
        assert list(tmp_path.rglob("scripts/run.py"))
        assert [s["hpc_jobs"] for s in listed.json() if s["name"] == "demo-skill"] == [
            []
        ]

    async def test_reports_the_hpc_jobs_a_skill_ships(self, session):
        """Loading such a skill lets the agent run its scripts on a cluster as
        the researcher, so the API says so, from the folder rather than from
        anything the skill declares about itself."""
        alice = await seed_user(session)
        headers = {"X-Vista-User-Email": alice.email}
        files, data = _multipart(
            {
                "demo-skill/SKILL.md": SKILL_MD,
                "demo-skill/hpc_jobs/primus-smoke/README.md": "# primus-smoke\n",
                "demo-skill/hpc_jobs/primus-smoke/job.lux.slurm": "echo hi\n",
            }
        )
        with api_client(session) as (client, _):
            response = await client.post(
                UPLOAD, files=files, data=data, headers=headers
            )
            assert response.status_code == 201, response.text
            assert response.json()["hpc_jobs"] == ["primus-smoke"]
            listed = await client.get("/skills", headers=headers)
            detail = await client.get("/skills/demo-skill", headers=headers)
        assert [s["hpc_jobs"] for s in listed.json() if s["name"] == "demo-skill"] == [
            ["primus-smoke"]
        ]
        assert detail.json()["hpc_jobs"] == ["primus-smoke"]

    async def test_loads_into_named_project(self, session):
        alice = await seed_user(session)
        project = await seed_project(session, alice, skills=["salt-analysis"])
        headers = {"X-Vista-User-Email": alice.email}
        files, data = _multipart({"demo-skill/SKILL.md": SKILL_MD})
        with api_client(session) as (client, _):
            response = await client.post(
                UPLOAD,
                files=files,
                data={**data, "project": project.name},
                headers=headers,
            )
            assert response.status_code == 201, response.text
            fetched = await client.get(f"/projects/{project.name}", headers=headers)
        assert fetched.json()["skills"] == ["salt-analysis", "demo-skill"]

    async def test_project_outside_access_is_refused_and_leaves_no_skill(
        self, session, tmp_path
    ):
        alice = await seed_user(session)
        bob = await seed_user(session)
        project = await seed_project(session, bob)
        headers = {"X-Vista-User-Email": alice.email}
        files, data = _multipart({"demo-skill/SKILL.md": SKILL_MD})
        with api_client(session) as (client, _):
            response = await client.post(
                UPLOAD,
                files=files,
                data={**data, "project": project.name},
                headers=headers,
            )
            listed = await client.get("/skills", headers=headers)
        assert response.status_code == 403, response.text
        assert "demo-skill" not in [s["name"] for s in listed.json()]
        assert not list(tmp_path.rglob("SKILL.md"))

    async def test_created_skill_loads_into_named_project(self, session):
        alice = await seed_user(session)
        project = await seed_project(session, alice)
        headers = {"X-Vista-User-Email": alice.email}
        with api_client(session) as (client, _):
            response = await client.post(
                "/skills",
                json={
                    "name": "chat-skill",
                    "description": "Made from a chat.",
                    "body": "# Chat skill",
                    "project": project.name,
                },
                headers=headers,
            )
            assert response.status_code == 201, response.text
            fetched = await client.get(f"/projects/{project.name}", headers=headers)
        assert fetched.json()["skills"] == ["chat-skill"]

    async def test_name_clash_is_409_and_leaves_no_copy(self, session, tmp_path):
        alice = await seed_user(session)
        headers = {"X-Vista-User-Email": alice.email}
        with api_client(session) as (client, _):
            files, data = _multipart({"demo-skill/SKILL.md": SKILL_MD})
            first = await client.post(UPLOAD, files=files, data=data, headers=headers)
            assert first.status_code == 201, first.text
            copies = len(list(tmp_path.rglob("SKILL.md")))

            files, data = _multipart({"other-folder/SKILL.md": SKILL_MD})
            second = await client.post(UPLOAD, files=files, data=data, headers=headers)
        assert second.status_code == 409, second.text
        assert len(list(tmp_path.rglob("SKILL.md"))) == copies

    async def test_bad_folder_is_400(self, session):
        alice = await seed_user(session)
        files, data = _multipart({"demo-skill/README.md": "no skill here"})
        with api_client(session) as (client, _):
            response = await client.post(
                UPLOAD,
                files=files,
                data=data,
                headers={"X-Vista-User-Email": alice.email},
            )
        assert response.status_code == 400
        assert "No SKILL.md" in response.json()["detail"]

    async def test_paths_must_match_files(self, session):
        alice = await seed_user(session)
        files, _ = _multipart({"demo/SKILL.md": SKILL_MD, "demo/a.txt": "a"})
        with api_client(session) as (client, _):
            response = await client.post(
                UPLOAD,
                files=files,
                data={"paths": ["demo/SKILL.md"]},
                headers={"X-Vista-User-Email": alice.email},
            )
        assert response.status_code == 400
        assert "paths" in response.json()["detail"]


AUTHOR_SKILL = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "vista_backend"
    / "db"
    / "skills"
    / "hpc-skill-author"
)


def _agent_writes_skill(
    output: Path, skill: str = "demo-hpc", job: str = "hello"
) -> Path:
    """What the `hpc-skill-author` skill tells the agent to do in its sandbox:
    copy the templates to /mnt/data/output/<skill>/ and fill in the names."""
    folder = output / skill
    (folder / "hpc_jobs").mkdir(parents=True)
    shutil.copy(AUTHOR_SKILL / "templates" / "skill" / "SKILL.md", folder / "SKILL.md")
    shutil.copytree(AUTHOR_SKILL / "templates" / "job", folder / "hpc_jobs" / job)
    for path in folder.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            path.write_text(
                text.replace("__SKILL__", skill).replace("__JOB__", job),
                encoding="utf-8",
            )
    return folder


@pytest.mark.anyio
class TestImportFromOutput:
    """`/projects/{project}/skills/from-output`: the Import modal's "From
    project output" tab, for a skill the agent wrote in its sandbox."""

    @pytest.fixture(autouse=True)
    def _data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "data_dir", tmp_path)

    async def _setup(self, session):
        alice = await seed_user(session)
        project = await seed_project(session, alice)
        output = agent_volume_root(project.id, alice.id) / "data" / "output"
        output.mkdir(parents=True)
        url = f"/projects/{project.name}/skills/from-output"
        return alice, project, output, url, {"X-Vista-User-Email": alice.email}

    async def _project_skills(self, session, project) -> list[str]:
        row = (
            await session.exec(
                select(ProjectTable).where(ProjectTable.id == project.id)
            )
        ).one()
        await session.refresh(row)
        return row.skills

    async def test_imports_loads_and_stages_an_hpc_skill(self, session, tmp_path):
        alice, project, output, url, headers = await self._setup(session)
        folder = _agent_writes_skill(output)
        (output / "notes").mkdir()  # not a skill: not listed
        (output / "plot.png").write_bytes(b"png")

        # The agent's own check, as it runs it in the sandbox.
        checked = subprocess.run(
            [
                sys.executable,
                str(AUTHOR_SKILL / "scripts" / "validate.py"),
                str(folder),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert checked.returncode == 0, checked.stdout

        with api_client(session) as (client, _):
            listed = await client.get(url, headers=headers)
            assert listed.status_code == 200, listed.text
            assert listed.json() == [
                {
                    "folder": "demo-hpc",
                    "name": "demo-hpc",
                    "error": None,
                    "hpc_jobs": ["hello"],
                    "exists": False,
                    "replaceable": False,
                }
            ]
            response = await client.post(
                url, json={"folder": "demo-hpc"}, headers=headers
            )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["name"] == "demo-hpc"
        assert body["is_public"] is False
        assert body["hpc_jobs"] == ["hello"]
        assert "demo-hpc/hello" in body["body"]

        # Loaded: in the project's skills, which is what the agent's skills
        # volume is built from, so the MCP server finds the job as demo-hpc/hello.
        assert await self._project_skills(session, project) == ["demo-hpc"]
        rows = (await session.exec(select(SkillTable))).all()
        volume = tmp_path / "skills-volume"
        stage_skills(
            volume,
            ["demo-hpc"],
            {row.name: settings.data_dir / row.path for row in rows},
        )
        job = volume / "demo-hpc" / "hpc_jobs" / "hello"
        assert (job / "job.lux.slurm").is_file()
        assert (job / "README.md").read_text(encoding="utf-8").startswith("# hello")

    async def test_symlink_is_refused(self, session, tmp_path):
        _, _, output, url, headers = await self._setup(session)
        secret = tmp_path / "secret.txt"
        secret.write_text("host file", encoding="utf-8")
        folder = _agent_writes_skill(output)
        (folder / "hpc_jobs" / "hello" / "data.txt").symlink_to(secret)
        with api_client(session) as (client, _):
            response = await client.post(
                url, json={"folder": "demo-hpc"}, headers=headers
            )
        assert response.status_code == 400, response.text
        assert "symlink" in response.json()["detail"]
        assert not (
            settings.storage_dir.exists() and any(settings.storage_dir.iterdir())
        )

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs")
    async def test_fifo_is_refused_without_blocking(self, session):
        _, _, output, url, headers = await self._setup(session)
        folder = _agent_writes_skill(output)
        os.mkfifo(folder / "hpc_jobs" / "hello" / "pipe")
        with api_client(session) as (client, _):
            listed = await client.get(url, headers=headers)
            response = await client.post(
                url, json={"folder": "demo-hpc"}, headers=headers
            )
        assert "not a regular file" in listed.json()[0]["error"]
        assert response.status_code == 400, response.text
        assert "not a regular file" in response.json()["detail"]

    async def test_listing_never_reads_through_a_symlink(self, session, tmp_path):
        _, _, output, url, headers = await self._setup(session)
        secret = tmp_path / "secret.env"
        secret.write_text("---\nname: [TOKEN=hunter2\n---\n", encoding="utf-8")
        folder = _agent_writes_skill(output)
        (folder / "SKILL.md").unlink()
        (folder / "SKILL.md").symlink_to(secret)
        with api_client(session) as (client, _):
            listed = await client.get(url, headers=headers)
        assert listed.status_code == 200, listed.text
        assert "hunter2" not in listed.text
        assert "symlink" in listed.json()[0]["error"]

    async def test_a_symlinked_output_folder_is_refused(self, session, tmp_path):
        _, _, output, url, headers = await self._setup(session)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        _agent_writes_skill(elsewhere)
        output.rmdir()
        output.symlink_to(elsewhere)
        with api_client(session) as (client, _):
            listed = await client.get(url, headers=headers)
            response = await client.post(
                url, json={"folder": "demo-hpc"}, headers=headers
            )
        assert listed.status_code == 400, listed.text
        assert response.status_code == 400, response.text

    async def test_symlinks_in_dropped_folders_are_ignored(self, session):
        _, _, output, url, headers = await self._setup(session)
        folder = _agent_writes_skill(output)
        (folder / ".venv" / "bin").mkdir(parents=True)
        (folder / ".venv" / "bin" / "python").symlink_to(sys.executable)
        with api_client(session) as (client, _):
            response = await client.post(
                url, json={"folder": "demo-hpc"}, headers=headers
            )
        assert response.status_code == 201, response.text

    @pytest.mark.parametrize("folder", ["../output", ".hidden", "a/b", "missing"])
    async def test_only_top_level_output_folders(self, session, folder):
        _, _, output, url, headers = await self._setup(session)
        _agent_writes_skill(output, skill="demo-hpc")
        (output / ".hidden").mkdir()
        with api_client(session) as (client, _):
            response = await client.post(url, json={"folder": folder}, headers=headers)
        assert response.status_code == 404, response.text

    async def test_name_clash_then_replace(self, session):
        _, project, output, url, headers = await self._setup(session)
        folder = _agent_writes_skill(output)
        with api_client(session) as (client, _):
            first = await client.post(url, json={"folder": "demo-hpc"}, headers=headers)
            assert first.status_code == 201, first.text

            # The agent fixes the skill: a second job, and a new description.
            shutil.copytree(
                folder / "hpc_jobs" / "hello", folder / "hpc_jobs" / "second"
            )
            md = folder / "SKILL.md"
            md.write_text(
                md.read_text(encoding="utf-8").replace("TODO: what", "Fixed: what"),
                encoding="utf-8",
            )
            clash = await client.post(url, json={"folder": "demo-hpc"}, headers=headers)
            assert clash.status_code == 409, clash.text
            assert "Replace" in clash.json()["detail"]
            listed = await client.get(url, headers=headers)
            assert listed.json()[0]["replaceable"] is True

            replaced = await client.post(
                url, json={"folder": "demo-hpc", "replace": True}, headers=headers
            )
        assert replaced.status_code == 201, replaced.text
        assert replaced.json()["hpc_jobs"] == ["hello", "second"]
        assert replaced.json()["description"].startswith("Fixed: what")
        assert replaced.json()["is_public"] is False
        assert await self._project_skills(session, project) == ["demo-hpc"]
        # One copy on disk: the old folder went with the swap.
        assert len(list(settings.storage_dir.iterdir())) == 1

    async def test_published_skill_is_never_replaced(self, session):
        _, _, output, url, headers = await self._setup(session)
        _agent_writes_skill(output)
        with api_client(session) as (client, _):
            first = await client.post(url, json={"folder": "demo-hpc"}, headers=headers)
            assert first.status_code == 201, first.text
            published = await client.patch(
                "/skills/demo-hpc", json={"is_public": True}, headers=headers
            )
            assert published.status_code == 200, published.text
            before = (await client.get("/skills/demo-hpc", headers=headers)).json()
            again = await client.post(
                url, json={"folder": "demo-hpc", "replace": True}, headers=headers
            )
            after = (await client.get("/skills/demo-hpc", headers=headers)).json()
        assert again.status_code == 409, again.text
        assert "published" in again.json()["detail"]
        assert after == before

    async def test_another_users_output_is_not_theirs(self, session):
        alice, project, output, url, _ = await self._setup(session)
        _agent_writes_skill(output)
        bob = await seed_user(session)
        await add_project_member(session, project.name, bob.email, alice)
        bob_headers = {"X-Vista-User-Email": bob.email}
        with api_client(session) as (client, _):
            listed = await client.get(url, headers=bob_headers)
            response = await client.post(
                url, json={"folder": "demo-hpc"}, headers=bob_headers
            )
        assert listed.json() == []
        assert response.status_code == 404, response.text

    async def test_non_member_is_refused(self, session):
        _, _, output, url, _ = await self._setup(session)
        _agent_writes_skill(output)
        mallory = await seed_user(session)
        with api_client(session) as (client, _):
            response = await client.get(
                url, headers={"X-Vista-User-Email": mallory.email}
            )
        assert response.status_code == 403, response.text


def test_the_sandbox_cannot_write_its_skills(tmp_path, monkeypatch):
    """The MCP server submits `<skill>/<job>` from the staged skills, so they are
    mounted read-only over the writable /mnt: otherwise the agent could add a
    cluster job to a loaded skill without the researcher importing anything."""
    import json

    from harness import make_project, make_user

    from vista_backend.agents.agents import ProjectAgent

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    agent = ProjectAgent(make_project(), make_user())
    [sandbox] = [
        s
        for s in agent._mcp_servers  # noqa: SLF001
        if "VISTA_DEV_MCP_VOLUMES" in (getattr(s, "env", None) or {})
    ]
    volumes = json.loads(sandbox.env["VISTA_DEV_MCP_VOLUMES"])
    assert [str(agent.volume_root), "/mnt", "w"] in volumes
    assert [str(agent.skills_volume_dir), "/mnt/skills", "r"] in volumes
