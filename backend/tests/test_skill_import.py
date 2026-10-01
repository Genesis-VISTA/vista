"""
Skill import: the GitHub URL parser, importing an uploaded local folder, and
the `POST /skills/import/upload` route that serves the Import modal's
"Local folder" tab.
"""

import io

import pytest
from harness import api_client, seed_user

from vista_backend.agents import skill_import
from vista_backend.agents.skill_import import (
    SkillImportError,
    import_skill_from_files,
    parse_github_url,
)
from vista_backend.config import settings

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
