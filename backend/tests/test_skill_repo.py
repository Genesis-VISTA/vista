"""
Importing skills from a repository on GitHub or a GitLab (`agents/skill_repo.py`,
`POST /skills/import`, `/skills/import/scan`, `/skills/import/batch`).

The hosts are faked at `urlopen`: `FakeHost` answers the GitHub and GitLab API
calls the import makes, serving a tarball built from a dict of files, so each
test states its repository's layout directly.
"""

import io
import json
import tarfile
import urllib.error
import urllib.parse

import pytest
from harness import api_client, seed_project, seed_user
from sqlmodel import select

from vista_backend.agents import skill_repo
from vista_backend.agents.skill_repo import (
    SkillRepoAuthError,
    SkillRepoError,
    SkillRepoUnreachableError,
    parse_repo_url,
)
from vista_backend.agents.skills import read_skill
from vista_backend.config import settings
from vista_backend.db.schemas import ProjectTable, SkillTable, UserTable

COMMIT = "0123456789abcdef0123456789abcdef01234567"


def skill_md(name: str, description: str = "Does a thing.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"


class _Response(io.BytesIO):
    def __init__(self, body: bytes, headers: dict[str, str]):
        super().__init__(body)
        self.status = 200
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _tarball(files: dict[str, str], links: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(f"repo-{COMMIT[:7]}/{path}")
            info.size = len(data)
            info.mode = 0o755 if path.endswith(".sh") else 0o644
            tar.addfile(info, io.BytesIO(data))
        for path, target in links.items():
            info = tarfile.TarInfo(f"repo-{COMMIT[:7]}/{path}")
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)
    return buf.getvalue()


class FakeHost:
    """
    One repository, served as GitHub (github.com) or as a GitLab on any host.
    `private` makes it answer 404 without `token`, and 401 for a wrong one.
    """

    def __init__(
        self,
        monkeypatch,
        files: dict[str, str],
        *,
        host: str = "github.com",
        project: str = "acme/skills",
        links: dict[str, str] | None = None,
        branches: tuple[str, ...] = ("main",),
        private: bool = False,
        token: str | None = None,
    ):
        self.files, self.links = files, links or {}
        self.host, self.project = host, project
        self.branches, self.private, self.token = branches, private, token
        self.requests: list = []
        monkeypatch.setattr(skill_repo.urllib.request, "urlopen", self.urlopen)

    def _fail(self, url: str, code: int, gitlab: bool):
        headers = {"X-Gitlab-Meta": "{}"} if gitlab else {}
        raise urllib.error.HTTPError(url, code, "no", headers, None)  # type: ignore[arg-type]

    def urlopen(self, req, timeout):
        self.requests.append(req)
        url = req.full_url
        parts = urllib.parse.urlsplit(url)
        gitlab = self.host != "github.com"
        auth = req.get_header("Authorization")
        if gitlab:
            if parts.netloc != self.host or not parts.path.startswith(
                "/api/v4/projects/"
            ):
                raise AssertionError(f"unexpected request {url}")
            rest = parts.path[len("/api/v4/projects/") :]
            project, _, tail = rest.partition("/")
            project = urllib.parse.unquote(project)
        else:
            if parts.netloc != "api.github.com":
                raise AssertionError(f"unexpected request {url}")
            segs = parts.path.split("/")  # "", "repos", owner, repo, ...
            project, tail = "/".join(segs[2:4]), "/".join(segs[4:])
        if self.private:
            if auth is None:
                self._fail(url, 404, gitlab)
            if auth != f"Bearer {self.token}":
                self._fail(url, 401, gitlab)
        if project != self.project:
            self._fail(url, 404, gitlab)
        headers = {"X-Gitlab-Meta": "{}"} if gitlab else {}
        if tail == "":
            body = {"default_branch": self.branches[0]}
            if gitlab:
                body["path_with_namespace"] = self.project
            return _Response(json.dumps(body).encode(), headers)
        commits = "repository/commits/" if gitlab else "commits/"
        if tail.startswith(commits):
            ref = urllib.parse.unquote(tail[len(commits) :])
            if ref not in self.branches and ref != COMMIT:
                self._fail(url, 404 if gitlab else 422, gitlab)
            body = json.dumps({"id": COMMIT}).encode() if gitlab else COMMIT.encode()
            return _Response(body, headers)
        if tail in (f"tarball/{COMMIT}", "repository/archive.tar.gz"):
            return _Response(_tarball(self.files, self.links), headers)
        self._fail(url, 404, gitlab)


# --------------------------------------------------------------------------- #
# URLs
# --------------------------------------------------------------------------- #


class TestParseRepoUrl:
    def test_github_repo(self):
        repo = parse_repo_url("https://github.com/owner/repo.git")
        assert (repo.kind, repo.project, repo.ref, repo.path) == (
            "github",
            "owner/repo",
            None,
            "",
        )

    def test_github_tree_folder(self):
        repo = parse_repo_url(
            "https://github.com/Genesis-VISTA/lux-skills/tree/main/skills/vllm-skill/"
        )
        assert (repo.ref, repo.path) == ("main", "skills/vllm-skill")

    def test_gitlab_nested_group_tree(self):
        repo = parse_repo_url(
            "https://git.example.org/genesis/sub/skills/-/tree/main/skills/hpc?ref_type=heads"
        )
        assert (repo.kind, repo.host, repo.project, repo.ref, repo.path) == (
            "gitlab",
            "git.example.org",
            "genesis/sub/skills",
            "main",
            "skills/hpc",
        )

    def test_a_blob_link_to_skill_md_names_its_folder(self):
        repo = parse_repo_url("https://git.example.org/g/r/-/blob/main/a/b/SKILL.md")
        assert repo.path == "a/b"

    @pytest.mark.parametrize(
        "url",
        [
            "/Users/me/skills/demo-skill",
            "http://github.com/o/r",
            "https://github.com/onlyowner",
            "https://github.com/o/r/tree/main/../etc",
            "https://github.com/o/r/issues/1",
        ],
    )
    def test_rejects(self, url):
        with pytest.raises(SkillRepoError):
            parse_repo_url(url)


# --------------------------------------------------------------------------- #
# Finding and staging skills
# --------------------------------------------------------------------------- #


def _scan(url: str, tmp_path, token: str | None = None):
    fetched = skill_repo.fetch(url, tmp_path / "repo", token=token)
    return fetched, skill_repo.discover(fetched)


def test_a_root_skill_is_the_only_skill(monkeypatch, tmp_path):
    """A SKILL.md at the root wins, even with skills in folders below it."""
    FakeHost(
        monkeypatch,
        {
            "SKILL.md": skill_md("root-skill"),
            "scripts/run.py": "print(1)\n",
            "examples/other/SKILL.md": skill_md("other"),
        },
    )
    _, paths = _scan("https://github.com/acme/skills", tmp_path)
    assert paths == [""]


def test_skills_are_found_at_any_depth(monkeypatch, tmp_path):
    FakeHost(
        monkeypatch,
        {
            "README.md": "# skills\n",
            "skills/academy/SKILL.md": skill_md("academy"),
            "skills/hpc-skills/slurm/SKILL.md": skill_md("slurm"),
            "skills/eval/harness/planner/SKILL.md": skill_md("planner"),
            # Inside a skill: part of it, not a skill of its own.
            "skills/academy/examples/SKILL.md": skill_md("nested"),
            "node_modules/pkg/SKILL.md": skill_md("clutter"),
            ".github/SKILL.md": skill_md("hidden"),
        },
    )
    _, paths = _scan("https://github.com/acme/skills", tmp_path)
    assert paths == [
        "skills/academy",
        "skills/eval/harness/planner",
        "skills/hpc-skills/slurm",
    ]


def test_a_manifest_says_which_folders_are_skills(monkeypatch, tmp_path):
    FakeHost(
        monkeypatch,
        {
            ".claude-plugin/marketplace.json": json.dumps(
                {"plugins": [{"source": "./", "skills": ["./skills/listed"]}]}
            ),
            "skills/listed/SKILL.md": skill_md("listed"),
            "skills/unlisted/SKILL.md": skill_md("unlisted"),
        },
    )
    _, paths = _scan("https://github.com/acme/skills", tmp_path)
    assert paths == ["skills/listed"]


def test_no_skill_md_anywhere(monkeypatch, tmp_path):
    FakeHost(monkeypatch, {"README.md": "# nothing\n", "src/main.py": "\n"})
    with pytest.raises(SkillRepoError, match="No SKILL.md found in the repository"):
        _scan("https://github.com/acme/skills", tmp_path)


def test_a_folder_link_on_gitlab_with_a_slash_in_its_branch(monkeypatch, tmp_path):
    FakeHost(
        monkeypatch,
        {
            "skills/a/SKILL.md": skill_md("a"),
            "skills/hpc/slurm/SKILL.md": skill_md("slurm"),
            "skills/hpc/pbs/SKILL.md": skill_md("pbs"),
        },
        host="git.example.org",
        project="genesis/genesis-skills",
        branches=("main", "feature/x"),
    )
    fetched, paths = _scan(
        "https://git.example.org/genesis/genesis-skills/-/tree/feature/x/skills/hpc",
        tmp_path,
    )
    assert (fetched.ref, fetched.url.path) == ("feature/x", "skills/hpc")
    assert paths == ["skills/hpc/pbs", "skills/hpc/slurm"]


def test_a_missing_folder(monkeypatch, tmp_path):
    FakeHost(monkeypatch, {"SKILL.md": skill_md("x")})
    with pytest.raises(SkillRepoError, match="no folder 'nope'"):
        _scan("https://github.com/acme/skills/tree/main/nope", tmp_path)


def test_a_missing_branch(monkeypatch, tmp_path):
    FakeHost(monkeypatch, {"SKILL.md": skill_md("x")})
    with pytest.raises(SkillRepoError, match="no branch, tag or commit 'dev'"):
        _scan("https://github.com/acme/skills/tree/dev", tmp_path)


class TestStage:
    def test_copies_the_whole_folder_and_what_it_links_to(self, monkeypatch, tmp_path):
        FakeHost(
            monkeypatch,
            {
                "LICENSE": "root license\n",
                "skills/group/ATTRIBUTION.md": "group attribution\n",
                "skills/group/eval/SKILL.md": skill_md("eval"),
                "skills/group/eval/scripts/run.sh": "echo run\n",
                "skills/group/eval/assets/data.csv": "a,b\n",
                "skills/group/gen/SKILL.md": skill_md("gen"),
                "skills/group/gen/scripts/common.sh": "echo common\n",
            },
            links={
                "skills/group/eval/scripts/common.sh": "../../gen/scripts/common.sh",
                "skills/group/eval/scripts/escape.sh": "../../../../../etc/passwd",
            },
        )
        fetched, _ = _scan("https://github.com/acme/skills", tmp_path)
        dest = tmp_path / "out"
        info = skill_repo.stage(fetched, "skills/group/eval", dest)

        assert info.error is None and info.name == "eval"
        assert (dest / "scripts" / "run.sh").read_text(encoding="utf-8") == "echo run\n"
        assert (dest / "scripts" / "run.sh").stat().st_mode & 0o111
        assert (dest / "assets" / "data.csv").exists()
        # The link inside the repository is copied in as the file it points at.
        common = dest / "scripts" / "common.sh"
        assert not common.is_symlink()
        assert common.read_text(encoding="utf-8") == "echo common\n"
        # The one leaving it is dropped, and said so.
        assert not (dest / "scripts" / "escape.sh").exists()
        assert any("escape.sh" in w and "left out" in w for w in info.warnings)
        # A group folder's and the root's license files come along.
        assert (dest / "ATTRIBUTION.md").read_text(
            encoding="utf-8"
        ) == "group attribution\n"
        assert (dest / "LICENSE").read_text(encoding="utf-8") == "root license\n"
        assert any("ATTRIBUTION.md (from skills/group/)" == i for i in info.included)
        assert any(i.startswith("scripts/common.sh (a link to") for i in info.included)

    def test_its_own_license_is_kept_over_an_inherited_one(self, monkeypatch, tmp_path):
        FakeHost(
            monkeypatch,
            {
                "LICENSE": "root\n",
                "s/SKILL.md": skill_md("s"),
                "s/LICENSE": "own\n",
            },
        )
        fetched, _ = _scan("https://github.com/acme/skills", tmp_path)
        info = skill_repo.stage(fetched, "s", tmp_path / "out")
        assert (tmp_path / "out" / "LICENSE").read_text(encoding="utf-8") == "own\n"
        assert info.included == []

    def test_flags_paths_out_of_its_folder(self, monkeypatch, tmp_path):
        FakeHost(
            monkeypatch,
            {
                "skills/a/SKILL.md": skill_md("a")
                + "See ../b/references/guide.md and ./scripts/x.py.\n",
                "skills/b/SKILL.md": skill_md("b"),
            },
        )
        fetched, _ = _scan("https://github.com/acme/skills", tmp_path)
        info = skill_repo.stage(fetched, "skills/a", tmp_path / "out")
        assert info.warnings == [
            "SKILL.md refers to ../b/references/guide.md, outside the skill's "
            "folder; it is not imported."
        ]

    @pytest.mark.parametrize(
        "text, message",
        [
            ("# no frontmatter\n", "must start with YAML frontmatter"),
            ("---\nname: x\ndescription: [unclosed\n---\n", "Invalid YAML"),
            (
                "---\nname: Not Kebab\ndescription: d\n---\n",
                "name: String should match",
            ),
            ("---\nname: no-description\n---\n", "description: Field required"),
        ],
    )
    def test_a_malformed_skill_md(self, monkeypatch, tmp_path, text, message):
        FakeHost(monkeypatch, {"skills/bad/SKILL.md": text})
        fetched, paths = _scan("https://github.com/acme/skills", tmp_path)
        assert paths == ["skills/bad"]
        info = skill_repo.stage(fetched, "skills/bad", tmp_path / "out")
        assert info.error is not None
        assert info.error.startswith("SKILL.md in skills/bad is invalid")
        assert message in info.error
        assert not (tmp_path / "out").exists()

    def test_over_the_upload_limits(self, monkeypatch, tmp_path):
        monkeypatch.setattr(skill_repo, "MAX_SKILL_FILES", 2)
        FakeHost(
            monkeypatch,
            {"SKILL.md": skill_md("big"), "a.txt": "a", "b.txt": "b"},
        )
        fetched, _ = _scan("https://github.com/acme/skills", tmp_path)
        info = skill_repo.stage(fetched, "", tmp_path / "out")
        assert info.error == "It has 3 files; a skill may have at most 2."


def test_rename_rewrites_only_the_name(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "---\nname: old\ndescription: d\nmetadata:\n  name: kept\n---\nname: body\n",
        encoding="utf-8",
    )
    skill_repo.rename_skill(tmp_path, "new-name")
    skill = read_skill(tmp_path)
    assert (skill.name, skill.metadata, skill.body) == (
        "new-name",
        {"name": "kept"},
        "name: body",
    )
    with pytest.raises(SkillRepoError, match="not a valid skill name"):
        skill_repo.rename_skill(tmp_path, "Bad Name")


# --------------------------------------------------------------------------- #
# Hosts, tokens and errors
# --------------------------------------------------------------------------- #


def test_the_token_goes_only_to_the_urls_host(monkeypatch, tmp_path):
    host = FakeHost(
        monkeypatch,
        {"SKILL.md": skill_md("x")},
        host="git.example.org",
        project="g/r",
        private=True,
        token="glpat-x",
    )
    _scan("https://git.example.org/g/r", tmp_path, token="glpat-x")
    assert host.requests
    for req in host.requests:
        assert urllib.parse.urlsplit(req.full_url).netloc == "git.example.org"
        assert req.get_header("Authorization") == "Bearer glpat-x"


def test_no_token_sends_no_header(monkeypatch, tmp_path):
    host = FakeHost(monkeypatch, {"SKILL.md": skill_md("x")})
    _scan("https://github.com/acme/skills", tmp_path)
    assert all(req.get_header("Authorization") is None for req in host.requests)


def test_a_private_repository_without_a_token(monkeypatch, tmp_path):
    """Both hosts answer 404, not 401, for a private repository you cannot see."""
    FakeHost(monkeypatch, {"SKILL.md": skill_md("x")}, private=True, token="t")
    with pytest.raises(SkillRepoAuthError, match="no public repository") as e:
        _scan("https://github.com/acme/skills", tmp_path)
    assert e.value.had_token is False


def test_a_wrong_token(monkeypatch, tmp_path):
    FakeHost(
        monkeypatch,
        {"SKILL.md": skill_md("x")},
        host="git.example.org",
        project="g/r",
        private=True,
        token="right",
    )
    with pytest.raises(SkillRepoAuthError, match=r"refused access \(401\)"):
        _scan("https://git.example.org/g/r", tmp_path, token="wrong")


def test_a_host_that_is_not_gitlab(monkeypatch, tmp_path):
    def urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "no", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(skill_repo.urllib.request, "urlopen", urlopen)
    with pytest.raises(SkillRepoError, match="did not answer as a GitLab server"):
        _scan("https://example.org/a/b", tmp_path)


def test_an_unreachable_host(monkeypatch, tmp_path):
    def urlopen(req, timeout):
        raise urllib.error.URLError("nodename nor servname provided")

    monkeypatch.setattr(skill_repo.urllib.request, "urlopen", urlopen)
    with pytest.raises(
        SkillRepoUnreachableError, match="Could not reach git.example.org"
    ):
        _scan("https://git.example.org/a/b", tmp_path)


def test_a_timeout(monkeypatch, tmp_path):
    def urlopen(req, timeout):
        raise TimeoutError()

    monkeypatch.setattr(skill_repo.urllib.request, "urlopen", urlopen)
    with pytest.raises(SkillRepoUnreachableError, match="did not answer in time"):
        _scan("https://github.com/acme/skills", tmp_path)


# --------------------------------------------------------------------------- #
# The routes
# --------------------------------------------------------------------------- #

IMPORT, SCAN, BATCH = "/skills/import", "/skills/import/scan", "/skills/import/batch"

MULTI = {
    "skills/primus-skill/SKILL.md": skill_md("primus-skill", "Train with Primus."),
    "skills/primus-skill/scripts/run.sh": "echo primus\n",
    "skills/vllm-skill/SKILL.md": skill_md("vllm-skill", "Serve with vLLM."),
    "skills/vllm-skill/hpc_jobs/serve/job.lux.slurm": "echo serve\n",
    "skills/broken/SKILL.md": "no frontmatter\n",
}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)


async def _skill(session, name: str) -> SkillTable | None:
    return (
        await session.exec(select(SkillTable).where(SkillTable.name == name))
    ).first()


@pytest.mark.anyio
class TestSingleImport:
    async def test_a_root_skill_imports_as_before(self, session, monkeypatch, tmp_path):
        FakeHost(
            monkeypatch,
            {"SKILL.md": skill_md("demo-skill"), "scripts/run.py": "print(1)\n"},
        )
        alice = await seed_user(session)
        url = "https://github.com/acme/skills"
        with api_client(session) as (client, _):
            response = await client.post(
                IMPORT, json={"url": url}, headers={"X-Vista-User-Email": alice.email}
            )
        assert response.status_code == 201, response.text
        body = response.json()
        assert (body["name"], body["is_public"], body["repo_url"]) == (
            "demo-skill",
            False,
            url,
        )
        row = await _skill(session, "demo-skill")
        assert row is not None
        assert (settings.data_dir / row.path / "scripts" / "run.py").exists()

    async def test_a_folder_link_imports_just_that_skill(self, session, monkeypatch):
        FakeHost(monkeypatch, MULTI)
        alice = await seed_user(session)
        with api_client(session) as (client, _):
            response = await client.post(
                IMPORT,
                json={
                    "url": "https://github.com/acme/skills/tree/main/skills/vllm-skill"
                },
                headers={"X-Vista-User-Email": alice.email},
            )
        assert response.status_code == 201, response.text
        assert response.json()["name"] == "vllm-skill"
        assert response.json()["hpc_jobs"] == ["serve"]
        assert await _skill(session, "primus-skill") is None

    async def test_a_repository_of_many_skills_is_refused_with_their_names(
        self, session, monkeypatch
    ):
        FakeHost(monkeypatch, MULTI)
        alice = await seed_user(session)
        with api_client(session) as (client, _):
            response = await client.post(
                IMPORT,
                json={"url": "https://github.com/acme/skills"},
                headers={"X-Vista-User-Email": alice.email},
            )
        assert response.status_code == 400
        assert "holds 3 skills" in response.json()["detail"]
        assert "primus-skill" in response.json()["detail"]

    async def test_a_taken_name_is_a_409(self, session, monkeypatch):
        FakeHost(monkeypatch, {"SKILL.md": skill_md("demo-skill")})
        alice = await seed_user(session)
        headers = {"X-Vista-User-Email": alice.email}
        with api_client(session) as (client, _):
            first = await client.post(
                IMPORT, json={"url": "https://github.com/acme/skills"}, headers=headers
            )
            again = await client.post(
                IMPORT, json={"url": "https://github.com/acme/skills"}, headers=headers
            )
        assert first.status_code == 201
        assert again.status_code == 409
        assert again.json()["detail"] == "Name already in use: demo-skill"

    async def test_malformed_skill_md_is_a_400(self, session, monkeypatch):
        FakeHost(monkeypatch, {"SKILL.md": "---\nname: x\n---\n"})
        alice = await seed_user(session)
        with api_client(session) as (client, _):
            response = await client.post(
                IMPORT,
                json={"url": "https://github.com/acme/skills"},
                headers={"X-Vista-User-Email": alice.email},
            )
        assert response.status_code == 400
        assert "SKILL.md in the repository root is invalid" in response.json()["detail"]
        assert "description" in response.json()["detail"]

    async def test_auth_failure_says_where_to_add_a_token(self, session, monkeypatch):
        FakeHost(
            monkeypatch,
            {"SKILL.md": skill_md("x")},
            host="git.example.org",
            project="g/r",
            private=True,
            token="t",
        )
        alice = await seed_user(session)
        with api_client(session) as (client, _):
            response = await client.post(
                IMPORT,
                json={"url": "https://git.example.org/g/r"},
                headers={"X-Vista-User-Email": alice.email},
            )
        assert response.status_code == 403
        assert "Add a token for it in the import dialog" in response.json()["detail"]

    async def test_an_unreachable_host_is_a_502(self, session, monkeypatch):
        def urlopen(req, timeout):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(skill_repo.urllib.request, "urlopen", urlopen)
        alice = await seed_user(session)
        with api_client(session) as (client, _):
            response = await client.post(
                IMPORT,
                json={"url": "https://git.example.org/g/r"},
                headers={"X-Vista-User-Email": alice.email},
            )
        assert response.status_code == 502
        assert "Could not reach git.example.org" in response.json()["detail"]


@pytest.mark.anyio
class TestTokens:
    async def test_a_token_in_the_request_is_sent_and_not_stored(
        self, session, monkeypatch
    ):
        host = FakeHost(
            monkeypatch,
            {"SKILL.md": skill_md("private-skill")},
            host="git.example.org",
            project="g/r",
            private=True,
            token="glpat-once",
        )
        alice = await seed_user(session)
        with api_client(session) as (client, _):
            response = await client.post(
                IMPORT,
                json={"url": "https://git.example.org/g/r", "token": "glpat-once"},
                headers={"X-Vista-User-Email": alice.email},
            )
        assert response.status_code == 201, response.text
        assert {r.get_header("Authorization") for r in host.requests} == {
            "Bearer glpat-once"
        }
        user = await session.get(UserTable, alice.id)
        assert user is not None
        assert (user.github_token, user.gitlab_token) == (None, None)

    async def test_the_settings_token_and_never_the_projects(
        self, session, alice, monkeypatch
    ):
        """The project is where the skill lands, not where its token comes from."""
        host = FakeHost(
            monkeypatch,
            {"SKILL.md": skill_md("x")},
            private=True,
            token="ghp_users",
        )
        row = await session.get(UserTable, alice.id)
        assert row is not None
        row.github_token = "ghp_users"
        session.add(row)
        await seed_project(session, alice, name="tokened")
        project = (
            await session.exec(
                select(ProjectTable).where(ProjectTable.name == "tokened")
            )
        ).one()
        project.forum_repo_token = "github_pat_forum"
        session.add(project)
        await session.commit()
        with api_client(session) as (client, _):
            response = await client.post(
                IMPORT,
                json={"url": "https://github.com/acme/skills", "project": "tokened"},
                headers={"X-Vista-User-Email": alice.email},
            )
        assert response.status_code == 201, response.text
        assert {r.get_header("Authorization") for r in host.requests} == {
            "Bearer ghp_users"
        }

    async def test_no_settings_token_goes_to_another_host(
        self, session, alice, monkeypatch
    ):
        host = FakeHost(
            monkeypatch,
            {"SKILL.md": skill_md("x")},
            host="git.example.org",
            project="g/r",
        )
        row = await session.get(UserTable, alice.id)
        assert row is not None
        row.github_token, row.gitlab_token = "ghp_x", "glpat_x"
        session.add(row)
        await session.commit()
        with api_client(session) as (client, _):
            response = await client.post(
                IMPORT,
                json={"url": "https://git.example.org/g/r"},
                headers={"X-Vista-User-Email": alice.email},
            )
        assert response.status_code == 201, response.text
        assert all(r.get_header("Authorization") is None for r in host.requests)


@pytest.mark.anyio
class TestScanAndBatch:
    async def test_scan_lists_each_skill(self, session, monkeypatch):
        FakeHost(monkeypatch, MULTI)
        alice = await seed_user(session)
        with api_client(session) as (client, _):
            response = await client.post(
                SCAN,
                json={"url": "https://github.com/acme/skills"},
                headers={"X-Vista-User-Email": alice.email},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["commit"] == COMMIT and body["ref"] == "main"
        listed = {s["path"]: s for s in body["skills"]}
        assert list(listed) == [
            "skills/broken",
            "skills/primus-skill",
            "skills/vllm-skill",
        ]
        assert listed["skills/vllm-skill"]["description"] == "Serve with vLLM."
        assert listed["skills/vllm-skill"]["hpc_jobs"] == ["serve"]
        assert listed["skills/vllm-skill"]["url"] == (
            "https://github.com/acme/skills/tree/main/skills/vllm-skill"
        )
        assert listed["skills/broken"]["name"] is None
        assert "invalid" in listed["skills/broken"]["error"]
        assert await _skill(session, "vllm-skill") is None

    async def test_import_several_into_a_project(self, session, monkeypatch):
        FakeHost(monkeypatch, MULTI)
        alice = await seed_user(session)
        await seed_project(session, alice, name="lux")
        with api_client(session) as (client, _):
            response = await client.post(
                BATCH,
                json={
                    "url": "https://github.com/acme/skills",
                    "commit": COMMIT,
                    "project": "lux",
                    "skills": [
                        {"path": "skills/primus-skill"},
                        {"path": "skills/vllm-skill"},
                        {"path": "skills/broken"},
                    ],
                },
                headers={"X-Vista-User-Email": alice.email},
            )
        assert response.status_code == 200, response.text
        results = response.json()
        assert [(r["name"], r["status"]) for r in results] == [
            ("primus-skill", "imported"),
            ("vllm-skill", "imported"),
            (None, "failed"),
        ]
        assert results[0]["skill"]["repo_url"] == (
            f"https://github.com/acme/skills/tree/{COMMIT}/skills/primus-skill"
        )
        row = (
            await session.exec(select(ProjectTable).where(ProjectTable.name == "lux"))
        ).one()
        await session.refresh(row)
        assert row.skills == ["primus-skill", "vllm-skill"]
        primus = await _skill(session, "primus-skill")
        assert primus is not None
        assert (settings.data_dir / primus.path / "scripts" / "run.sh").exists()

    @pytest.fixture
    async def installed(self, session, monkeypatch):
        """alice, with a private `vllm-skill` already installed from elsewhere."""
        FakeHost(monkeypatch, {"SKILL.md": skill_md("vllm-skill", "The old one.")})
        alice = await seed_user(session)
        with api_client(session) as (client, _):
            response = await client.post(
                IMPORT,
                json={"url": "https://github.com/acme/skills"},
                headers={"X-Vista-User-Email": alice.email},
            )
        assert response.status_code == 201
        FakeHost(monkeypatch, MULTI)
        return alice

    async def _batch(self, session, alice, choice: dict):
        with api_client(session) as (client, _):
            scan = await client.post(
                SCAN,
                json={"url": "https://github.com/acme/skills"},
                headers={"X-Vista-User-Email": alice.email},
            )
            vllm = next(
                s for s in scan.json()["skills"] if s["path"] == "skills/vllm-skill"
            )
            assert (vllm["exists"], vllm["replaceable"]) == (True, True)
            response = await client.post(
                BATCH,
                json={
                    "url": "https://github.com/acme/skills",
                    "skills": [{"path": "skills/vllm-skill", **choice}],
                },
                headers={"X-Vista-User-Email": alice.email},
            )
        assert response.status_code == 200, response.text
        [result] = response.json()
        return result

    async def test_conflict_without_a_choice_fails(self, session, installed):
        result = await self._batch(session, installed, {})
        assert result["status"] == "failed"
        assert "skip, overwrite or rename" in result["detail"]

    async def test_conflict_skip(self, session, installed):
        result = await self._batch(session, installed, {"on_conflict": "skip"})
        assert result["status"] == "skipped"
        row = await _skill(session, "vllm-skill")
        assert row is not None and row.description == "The old one."

    async def test_conflict_overwrite(self, session, installed):
        old = await _skill(session, "vllm-skill")
        assert old is not None
        old_path = settings.data_dir / old.path
        result = await self._batch(session, installed, {"on_conflict": "overwrite"})
        assert result["status"] == "overwritten"
        session.expire_all()
        row = await _skill(session, "vllm-skill")
        assert row is not None and row.description == "Serve with vLLM."
        assert row.id == old.id
        assert (settings.data_dir / row.path / "hpc_jobs" / "serve").is_dir()
        assert not old_path.exists()

    async def test_conflict_rename(self, session, installed):
        result = await self._batch(
            session, installed, {"on_conflict": "rename", "rename_to": "vllm-lux"}
        )
        assert (result["status"], result["name"]) == ("renamed", "vllm-lux")
        renamed = await _skill(session, "vllm-lux")
        assert renamed is not None
        assert read_skill(settings.data_dir / renamed.path).name == "vllm-lux"
        old = await _skill(session, "vllm-skill")
        assert old is not None and old.description == "The old one."

    async def test_a_published_skill_cannot_be_overwritten(self, session, installed):
        row = await _skill(session, "vllm-skill")
        assert row is not None
        row.is_public = True
        session.add(row)
        await session.commit()
        with api_client(session) as (client, _):
            response = await client.post(
                BATCH,
                json={
                    "url": "https://github.com/acme/skills",
                    "skills": [
                        {"path": "skills/vllm-skill", "on_conflict": "overwrite"}
                    ],
                },
                headers={"X-Vista-User-Email": installed.email},
            )
        [result] = response.json()
        assert result["status"] == "failed"
        assert "published" in result["detail"]

    async def test_two_choices_with_one_name_are_refused(self, session, monkeypatch):
        FakeHost(
            monkeypatch,
            {
                "a/SKILL.md": skill_md("same"),
                "b/SKILL.md": skill_md("same"),
            },
        )
        alice = await seed_user(session)
        with api_client(session) as (client, _):
            response = await client.post(
                BATCH,
                json={
                    "url": "https://github.com/acme/skills",
                    "skills": [{"path": "a"}, {"path": "b"}],
                },
                headers={"X-Vista-User-Email": alice.email},
            )
        assert response.status_code == 409
        assert "would all be named same" in response.json()["detail"]
        assert await _skill(session, "same") is None

    async def test_a_path_the_repository_does_not_hold(self, session, monkeypatch):
        FakeHost(monkeypatch, MULTI)
        alice = await seed_user(session)
        with api_client(session) as (client, _):
            response = await client.post(
                BATCH,
                json={
                    "url": "https://github.com/acme/skills",
                    "skills": [{"path": "skills/nope"}],
                },
                headers={"X-Vista-User-Email": alice.email},
            )
        assert response.status_code == 400
        assert "No skill at 'skills/nope'" in response.json()["detail"]
