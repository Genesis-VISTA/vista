"""
The backend fetch behind the agent's `fetch_repository` tool: a github.com or
gitlab.com repository, pinned to a commit, unpacked into the agent's workspace
(`/mnt/data/work/<name>/`) for the `hpc-skill-author` skill to wrap. The
backend fetches so that the sandbox needs neither credentials nor trust in the
network's proxy; these tests fake the two APIs.
"""

import io
import json
import tarfile

import pytest
from pydantic_ai.messages import FunctionToolResultEvent

from harness import agent_under_test, call, make_project, make_user, say, step_model
from vista_backend.agents import source_fetch
from vista_backend.agents.source_fetch import SourceFetchError, parse_remote
from vista_backend.config import settings

SHA = "0123456789abcdef0123456789abcdef01234567"


def _tar_gz(members: dict[str, str], top: str = "top-0123") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in members.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


TREE = {
    "README.md": "# templates\n",
    "frontier/run.sbatch": "#!/bin/bash\n#SBATCH -N 2\nsrun python train.py\n",
    "frontier/setup.sh": "#!/bin/bash\necho setup\n",
    "perlmutter/run.sbatch": "#!/bin/bash\n",
}


class FakeApis:
    """The GitHub and GitLab endpoints `source_fetch` calls, recording each request."""

    def __init__(self, *, private: bool = False, tree: dict[str, str] | None = None):
        self.private = private
        self.tree = tree or TREE
        self.calls: list[tuple[str, str | None]] = []

    def __call__(
        self,
        url: str,
        host: str,
        *,
        token: str | None = None,
        limit: int = 10 * 1024 * 1024,
    ) -> bytes:
        self.calls.append((url, token))
        gl = "https://gitlab.com/api/v4/projects/amsc2%2Fai-services%2Ftemplates"
        gh = "https://api.github.com/repos/o/melt"
        routes = {
            gl: {
                "default_branch": "main",
                "visibility": "private" if self.private else "public",
            },
            f"{gl}/repository/commits/main": {"id": SHA},
            f"{gl}/repository/commits/v2": {"id": SHA},
            gh: {"default_branch": "dev", "private": self.private},
            f"{gh}/commits/dev": {"sha": SHA},
        }
        if url in routes:
            return json.dumps(routes[url]).encode()
        if url in (f"{gl}/repository/archive.tar.gz?sha={SHA}", f"{gh}/tarball/{SHA}"):
            data = _tar_gz(self.tree)
            if len(data) > limit:
                raise SourceFetchError("too large")
            return data
        raise AssertionError(f"unexpected request {url}")


@pytest.fixture
def apis(monkeypatch):
    fake = FakeApis()
    monkeypatch.setattr(source_fetch, "_request", fake)
    return fake


class TestParseRemote:
    @pytest.mark.parametrize(
        ("url", "host", "project", "ref", "subpath"),
        [
            ("https://github.com/o/r.git", "github", "o/r", None, ""),
            (
                "https://github.com/o/r/tree/v1.2/sub/dir/",
                "github",
                "o/r",
                "v1.2",
                "sub/dir",
            ),
            ("https://gitlab.com/g/r", "gitlab", "g/r", None, ""),
            (
                "https://gitlab.com/g/sub/deeper/r.git",
                "gitlab",
                "g/sub/deeper/r",
                None,
                "",
            ),
            ("https://gitlab.com/g/sub/r/-/tree/v2.0", "gitlab", "g/sub/r", "v2.0", ""),
            (
                "https://gitlab.com/g/r/-/tree/main/src/app?ref_type=heads",
                "gitlab",
                "g/r",
                "main",
                "src/app",
            ),
        ],
    )
    def test_github_and_gitlab(self, url, host, project, ref, subpath):
        remote = parse_remote(url)
        assert (remote.host, remote.project, remote.ref, remote.subpath) == (
            host,
            project,
            ref,
            subpath,
        )

    def test_ref_argument_wins(self):
        """How a GitLab branch with a `/` in its name gets through."""
        assert (
            parse_remote("https://gitlab.com/g/r", ref="feature/x").ref == "feature/x"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "https://gitlab.com/r",
            "https://gitlab.com/g/r/-/blob/main/README.md",
            "https://gitlab.com/g/../r",
            "https://github.com/o/r/tree/main/../../etc",
            "https://bitbucket.org/o/r",
            "https://gitlab.example.org/g/r",
            "/etc/passwd",
        ],
    )
    def test_refuses_everything_else(self, url):
        with pytest.raises(SourceFetchError):
            parse_remote(url)

    def test_only_the_two_apis_are_requested(self):
        for url in (
            "https://example.org/api/v4/projects/x",
            "http://gitlab.com/api/v4/projects/x",
            "https://gitlab.example.org/api/v4/projects/x",
        ):
            with pytest.raises(SourceFetchError, match="Refusing to fetch"):
                source_fetch._request(url, "gitlab")


class TestFetchRepository:
    def test_gitlab_subfolder_pinned_to_a_commit(self, tmp_path, apis):
        record = source_fetch.fetch_repository(
            "https://gitlab.com/amsc2/ai-services/templates/-/tree/main/frontier",
            tmp_path,
        )
        assert record == {
            "kind": "gitlab",
            "url": "https://gitlab.com/amsc2/ai-services/templates",
            "clone_url": "https://gitlab.com/amsc2/ai-services/templates.git",
            "ref": "main",
            "commit": SHA,
            "subpath": "frontier",
            "private": False,
            "files": 2,
            "folder": "templates-frontier",
        }
        dest = tmp_path / "templates-frontier"
        assert sorted(p.name for p in dest.iterdir()) == [
            "SOURCE.json",
            "run.sbatch",
            "setup.sh",
        ]
        assert (dest / "setup.sh").stat().st_mode & 0o111
        # Downloaded by the commit, never by the moving branch name.
        assert apis.calls[-1][0].endswith(f"?sha={SHA}")

    def test_github_default_branch(self, tmp_path, apis):
        record = source_fetch.fetch_repository("https://github.com/o/melt", tmp_path)
        assert (record["ref"], record["commit"], record["folder"]) == (
            "dev",
            SHA,
            "melt",
        )
        assert (tmp_path / "melt" / "frontier" / "run.sbatch").is_file()

    def test_the_token_goes_with_every_request_for_its_repository(self, tmp_path, apis):
        source_fetch.fetch_repository(
            "https://gitlab.com/amsc2/ai-services/templates",
            tmp_path,
            token="glpat-secret",
        )
        source_fetch.fetch_repository("https://github.com/o/melt", tmp_path)
        assert {
            tok for url, tok in apis.calls if url.startswith("https://gitlab.com/")
        } == {"glpat-secret"}
        assert {
            tok for url, tok in apis.calls if url.startswith("https://api.github.com/")
        } == {None}

    @pytest.mark.anyio
    async def test_the_agent_sends_each_host_its_own_token_and_never_the_forums(
        self, session, alice
    ):
        """
        Which token a fetch gets is `user_repo_token`'s choice, from the user's row:
        GitHub's to GitHub, gitlab.com's to gitlab.com, and a project's forum token
        to neither, since no project is ever passed.
        """
        from vista_backend.db.schemas import UserTable
        from vista_backend.services.repo_tokens import user_repo_token

        row = await session.get(UserTable, alice.id)
        row.github_token, row.gitlab_token = "ghp_users", "glpat-users"
        session.add(row)
        await session.flush()

        github = await user_repo_token(session, "https://github.com/o/melt", alice.id)
        gitlab = await user_repo_token(session, "https://gitlab.com/g/r", alice.id)
        other = await user_repo_token(session, "https://code.example.org/g/r", alice.id)

        assert github is not None and github.value == "ghp_users"
        assert gitlab is not None and gitlab.value == "glpat-users"
        assert other is None

    def test_private_repository_is_reported(self, tmp_path, apis):
        apis.private = True
        record = source_fetch.fetch_repository(
            "https://gitlab.com/amsc2/ai-services/templates", tmp_path
        )
        assert record["private"] is True

    def test_refetch_replaces_the_folder(self, tmp_path, apis):
        source_fetch.fetch_repository("https://github.com/o/melt", tmp_path)
        (tmp_path / "melt" / "stale.txt").write_text("old", encoding="utf-8")
        source_fetch.fetch_repository("https://github.com/o/melt", tmp_path)
        assert not (tmp_path / "melt" / "stale.txt").exists()
        assert sorted(p.name for p in tmp_path.iterdir()) == ["melt"]

    @pytest.mark.parametrize("bad", ["../escape.sh", "a/../../escape.sh"])
    def test_unsafe_archive_writes_nothing(self, tmp_path, monkeypatch, bad):
        fake = FakeApis(tree={"ok.txt": "x", bad: "x"})
        monkeypatch.setattr(source_fetch, "_request", fake)
        with pytest.raises(SourceFetchError, match="Unsafe path"):
            source_fetch.fetch_repository(
                "https://github.com/o/melt", tmp_path / "work"
            )
        assert not list(tmp_path.rglob("escape.sh"))
        assert not (tmp_path / "work" / "melt").exists()

    def test_missing_subfolder(self, tmp_path, apis):
        with pytest.raises(SourceFetchError, match="has no files"):
            source_fetch.fetch_repository(
                "https://github.com/o/melt/tree/dev/nowhere", tmp_path
            )

    def test_size_limit(self, tmp_path, apis, monkeypatch):
        monkeypatch.setattr(source_fetch, "MAX_UNPACKED_BYTES", 10)
        with pytest.raises(SourceFetchError, match="larger than"):
            source_fetch.fetch_repository("https://github.com/o/melt", tmp_path)


@pytest.mark.anyio
async def test_agent_tool_fetches_into_its_own_workspace(tmp_path, apis, monkeypatch):
    """The agent asks by URL; the files land where its sandbox sees /mnt/data/work."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    project = make_project(tools=["*"])
    model = step_model(
        [
            call(
                "fetch_repository",
                url="https://gitlab.com/amsc2/ai-services/templates/-/tree/main/frontier",
            ),
            say("fetched"),
        ]
    )
    with agent_under_test(project, make_user(), model) as (agent, _):
        events = [
            e async for e in agent.run_stream(user_prompt="wrap the frontier script")
        ]
        work = agent.output_dir.parent / "work"
    results = [e.part.content for e in events if isinstance(e, FunctionToolResultEvent)]
    assert len(results) == 1
    assert "Fetched into /mnt/data/work/templates-frontier/ (2 files)" in results[0]
    assert f'"commit": "{SHA}"' in results[0]
    assert (work / "templates-frontier" / "run.sbatch").is_file()
    assert work.is_relative_to(tmp_path / "volumes")


@pytest.mark.anyio
async def test_agent_tool_error_is_a_retry_not_a_crash(tmp_path, apis, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    model = step_model(
        [call("fetch_repository", url="https://bitbucket.org/o/r"), say("cannot")]
    )
    with agent_under_test(make_project(tools=["*"]), make_user(), model) as (agent, _):
        events = [e async for e in agent.run_stream(user_prompt="wrap it")]
    retry = [e.part.content for e in events if isinstance(e, FunctionToolResultEvent)]
    assert retry and "not a github.com or gitlab.com repository URL" in str(retry[0])
