"""
Hosted forums: their routes, and git through a real server on loopback.

The git tests run `git` itself against a `uvicorn` server in `dev-headers` mode
(the credential is an `http.extraHeader`, as the backend sends it), so what is
checked is what a VISTA install would meet: the smart HTTP passthrough, the
rules of `services/forum_hook.py` and the push records.
"""

import json
import os
import socket
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn

from vista_collaboration_server.api.app import create_app
from vista_collaboration_server.config import CollabSettings
from vista_collaboration_server.services import forums
from vista_collaboration_server.services.identity import (
    DISPLAY_NAME_HEADER,
    INSTALL_ID_HEADER,
)

from conftest import INSTALL_ID, OTHER_INSTALL_ID, PUBLIC, headers

THIRD_INSTALL_ID = uuid.UUID("00000000-0000-4000-8000-000000000003")

needs_git = pytest.mark.skipif(
    not forums.git_usable(), reason="needs git 2.34 or later"
)

ROSTER = {
    "proposer": "vista-proposer-1",
    "reviewer": "vista-reviewer-1",
    "referee": "vista-referee-1",
}


# ---------------------------------------------------------------------------
# A real server, and real git
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Live:
    """The server running on a loopback port, and a client for each of its users."""

    def __init__(self, settings: CollabSettings, port: int):
        self.settings = settings
        self.url = f"http://127.0.0.1:{port}"

    def api(
        self, install: uuid.UUID = INSTALL_ID, name: str | None = "Ada"
    ) -> httpx.Client:
        return httpx.Client(base_url=self.url, headers=headers(install, name))

    def db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.settings.data_dir / "collaboration.db")

    def wait_for_records(self, forum_url: str) -> None:
        """
        git tells a client its push is accepted before the server's
        `post-receive` has written the records: wait for the hook to finish.
        """
        forum_id = forum_url.rsplit("/", 1)[-1].removesuffix(".git")
        pending = self.settings.forums_dir / f"{forum_id}.git" / "vista-pending"
        deadline = time.monotonic() + 30
        while pending.is_dir() and any(pending.iterdir()):
            assert time.monotonic() < deadline, "post-receive did not finish"
            time.sleep(0.02)

    def pushes(self, forum_id: str) -> list[dict]:
        with self.api() as api:
            res = api.get(f"/forums/{forum_id}/pushes")
        assert res.status_code == 200, res.text
        return res.json()


@pytest.fixture
def live(tmp_path) -> Live:
    data_dir = tmp_path / "collab"
    settings = CollabSettings(
        data_dir=data_dir,
        database_url=f"sqlite+aiosqlite:///{data_dir / 'collaboration.db'}",
        auth_mode="dev-headers",
        forum_run_expiry_seconds=2700,
    )
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings), host="127.0.0.1", port=port, log_level="warning"
        )
    )
    server.install_signal_handlers = lambda: None  # not the main thread
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline, "server did not start"
        time.sleep(0.02)
    try:
        yield Live(settings, port)
    finally:
        server.should_exit = True
        thread.join(timeout=30)


class Install:
    """A researcher's clone of a forum: local commits, and pushes as their account."""

    def __init__(self, live: Live, tmp_path: Path, name: str, install: uuid.UUID):
        self.live = live
        self.install = install
        self.path = tmp_path / f"clone-{name}"
        self.path.mkdir()
        self.display = name
        self.env = {
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "vista-forum",
            "GIT_AUTHOR_EMAIL": "h@vista-forum.invalid",
            "GIT_COMMITTER_NAME": "vista-forum",
            "GIT_COMMITTER_EMAIL": "h@vista-forum.invalid",
        }
        self.git("init", "--quiet", "-b", "scratch")

    def git(self, *args: str, check: bool = True, cwd: Path | None = None):
        extra = [
            "-c",
            f"http.extraHeader={INSTALL_ID_HEADER}: {self.install}",
            "-c",
            f"http.extraHeader={DISPLAY_NAME_HEADER}: {self.display}",
        ]
        res = subprocess.run(
            ["git", *extra, *args],
            cwd=cwd or self.path,
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if check:
            assert res.returncode == 0, f"git {args}: {res.stderr}"
        return res

    def commit(self, files: dict[str, dict], message: str) -> str:
        for rel, content in files.items():
            path = self.path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(content), encoding="utf-8")
            self.git("add", rel)
        self.git("commit", "--quiet", "-m", message)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def open_thread(self, thread: str, extra: dict[str, dict] | None = None) -> None:
        """A new local branch `thread`, rooted like the lab's: thread.json and a TASK."""
        self.git("switch", "--quiet", "--orphan", thread)
        task = _post(thread, "TASK")
        self.commit(
            {
                "thread.json": {"v": 1, "id": thread, "title": "t", "created_by": "h"},
                f"posts/{task['id']}.json": task,
                **(extra or {}),
            },
            f"THREAD {thread}",
        )

    def post(self, thread: str, kind: str = "FINDING", **fields) -> dict:
        self.git("switch", "--quiet", thread)
        post = _post(thread, kind, **fields)
        self.commit({f"posts/{post['id']}.json": post}, f"{kind} {post['id']}")
        return post

    def ref(self, thread: str) -> str:
        return f"refs/heads/vista-forum/threads/{thread}"

    def push(self, forum_url: str, thread: str, *options: str, force: bool = False):
        args = ["push", *(["--force"] if force else [])]
        for option in options:
            args += ["-o", option]
        res = self.git(*args, forum_url, f"{thread}:{self.ref(thread)}", check=False)
        self.live.wait_for_records(forum_url)
        return res


def _post(thread: str, kind: str, identity: str = "human", **fields) -> dict:
    return {
        "v": 1,
        "id": str(uuid.uuid4()),
        "thread": thread,
        "kind": kind,
        "body": "x",
        "identity": identity,
        "role": "human",
        "origin": "o" * 32,
        "ts": datetime.now(UTC).isoformat(),
        "reply_to": None,
        "attachments": [],
        **fields,
    }


def _run_post(rounds: int = 2, roster: dict | None = None) -> dict:
    return {"run": {"rounds": rounds, "roster": roster or ROSTER}}


class Forum:
    """A hosted forum of Ada's, linked to a project in a team that Grace is in."""

    def __init__(self, live: Live, tmp_path: Path):
        self.live = live
        with live.api() as ada, live.api(OTHER_INSTALL_ID, "Grace") as grace:
            grace.get("/me")  # signed in once, so she can be added
            self.team = ada.post("/teams", json={"name": "salts"}).json()
            ada.post(
                f"/teams/{self.team['id']}/members", json={"sub": str(OTHER_INSTALL_ID)}
            ).raise_for_status()
            forum = ada.post("/forums")
            assert forum.status_code == 201, forum.text
            self.id = forum.json()["id"]
            self.url = forum.json()["git_url"]
            item = ada.post(
                "/items",
                json={
                    "kind": "project",
                    "name": "p",
                    "team_id": self.team["id"],
                    "manifest": {"forum": "hosted"},
                },
            ).json()
            self.item_id = item["id"]
            link = ada.put(f"/forums/{self.id}/item", json={"item_id": self.item_id})
            assert link.status_code == 200, link.text
        self.ada = Install(live, tmp_path, "Ada", INSTALL_ID)
        self.grace = Install(live, tmp_path, "Grace", OTHER_INSTALL_ID)

    def set_received_at(self, post_id: str, at: datetime) -> None:
        with self.live.db() as db:
            db.execute(
                "UPDATE forum_push SET received_at = ? WHERE post_id = ?",
                (at.isoformat(timespec="microseconds"), post_id),
            )


@pytest.fixture
def forum(live, tmp_path) -> Forum:
    return Forum(live, tmp_path)


def _ok(res) -> None:
    assert res.returncode == 0, res.stderr


def _refused(res, *words: str) -> None:
    assert res.returncode != 0, res.stderr
    assert "remote:" in res.stderr, res.stderr
    for word in words:
        assert word in res.stderr, res.stderr


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


@needs_git
def test_a_member_clones_and_pushes(forum, tmp_path):
    forum.ada.open_thread("t1")
    _ok(forum.ada.push(forum.url, "t1"))
    clone = tmp_path / "grace-clone"
    forum.grace.git("clone", "--quiet", forum.url, str(clone), cwd=tmp_path)
    refs = forum.grace.git("branch", "-r", cwd=clone).stdout
    assert "origin/vista-forum/threads/t1" in refs
    # Grace posts on top of Ada's thread.
    forum.grace.git("fetch", "--quiet", forum.url, f"{forum.ada.ref('t1')}:t1")
    forum.grace.git("switch", "--quiet", "t1")
    forum.grace.post("t1", "FINDING")
    _ok(forum.grace.push(forum.url, "t1"))


@needs_git
def test_a_non_member_is_told_404(forum, live, tmp_path):
    forum.ada.open_thread("t1")
    _ok(forum.ada.push(forum.url, "t1"))
    carol = Install(live, tmp_path, "Carol", THIRD_INSTALL_ID)
    with live.api(THIRD_INSTALL_ID, "Carol") as api:
        for path in (
            f"/git/{forum.id}.git/info/refs?service=git-upload-pack",
            f"/git/{forum.id}.git/info/refs?service=git-receive-pack",
            f"/forums/{forum.id}/pushes",
        ):
            assert api.get(path).status_code == 404, path
        assert api.delete(f"/forums/{forum.id}").status_code == 404
        # Nor is it different from one that is not there.
        assert api.get(f"/git/{uuid.uuid4()}.git/info/refs").status_code == 404
        assert api.get("/git/not-a-uuid.git/info/refs").status_code == 404
        assert api.get("/git/..%2f..%2fetc.git/info/refs").status_code == 404
    res = carol.git("ls-remote", forum.url, check=False)
    assert res.returncode != 0 and "not found" in res.stderr
    res = carol.push(forum.url, "scratch")
    assert res.returncode != 0


@needs_git
def test_no_sign_in_is_refused_before_git_runs(forum, live, monkeypatch):
    ran = []
    real = subprocess.Popen
    # git http-backend runs through asyncio in the server's own process.
    import asyncio

    original = asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        ran.append(args)
        return await original(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    assert real  # keep the import honest
    res = httpx.get(f"{live.url}/git/{forum.id}.git/info/refs?service=git-upload-pack")
    assert res.status_code == 401
    assert ran == []


@needs_git
def test_a_private_forum_is_its_owners_alone(live, tmp_path):
    with live.api() as ada, live.api(OTHER_INSTALL_ID, "Grace") as grace:
        forum = ada.post("/forums").json()
        path = f"/git/{forum['id']}.git/info/refs?service=git-upload-pack"
        assert ada.get(path).status_code == 200
        assert grace.get(path).status_code == 404
        assert grace.get(f"/forums/{forum['id']}/pushes").status_code == 404


@needs_git
def test_ids_are_uuids_before_any_path_is_built(live):
    with live.api() as api:
        for bad in ("..", "%2e%2e", "A" * 5, str(uuid.uuid4()).upper()):
            assert api.get(f"/git/{bad}.git/info/refs").status_code in (404, 422)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@needs_git
def test_only_thread_refs_are_accepted(forum):
    forum.ada.open_thread("t1")
    res = forum.ada.git("push", forum.url, "t1:refs/heads/main", check=False)
    _refused(res, "vista-forum/threads")
    res = forum.ada.git(
        "push", forum.url, "t1:refs/heads/vista-forum/other/t1", check=False
    )
    _refused(res, "vista-forum/threads")
    res = forum.ada.git("push", forum.url, "t1:refs/tags/v1", check=False)
    _refused(res, "vista-forum/threads")
    assert forum.live.pushes(forum.id) == []


@needs_git
def test_force_push_is_refused_and_changes_nothing(forum):
    forum.ada.open_thread("t1")
    forum.ada.post("t1")
    _ok(forum.ada.push(forum.url, "t1"))
    tip = forum.ada.git("rev-parse", "t1").stdout.strip()
    forum.ada.git("reset", "--quiet", "--hard", "HEAD~1")
    forum.ada.post("t1", "RISK")
    _refused(forum.ada.push(forum.url, "t1", force=True), "append-only")
    remote = forum.ada.git("ls-remote", forum.url).stdout
    assert f"{tip}\t{forum.ada.ref('t1')}" in remote


@needs_git
def test_deleting_a_thread_is_refused(forum):
    forum.ada.open_thread("t1")
    _ok(forum.ada.push(forum.url, "t1"))
    res = forum.ada.git("push", forum.url, f":{forum.ada.ref('t1')}", check=False)
    _refused(res, "deleted")
    assert forum.ada.ref("t1") in forum.ada.git("ls-remote", forum.url).stdout


@needs_git
def test_only_the_opener_closes_a_thread(forum):
    forum.ada.open_thread("t1")
    _ok(forum.ada.push(forum.url, "t1"))
    forum.grace.git("fetch", "--quiet", forum.url, f"{forum.ada.ref('t1')}:t1")
    forum.grace.git("switch", "--quiet", "t1")
    forum.grace.post("t1", "CLOSED")
    _refused(forum.grace.push(forum.url, "t1"), "opener")
    # Ada, who pushed the root, can.
    forum.ada.post("t1", "CLOSED")
    _ok(forum.ada.push(forum.url, "t1"))
    kinds = [p["kind"] for p in forum.live.pushes(forum.id)]
    assert kinds.count("CLOSED") == 1


@needs_git
def test_a_thread_opened_with_its_own_close_is_the_openers(forum):
    forum.grace.open_thread("t2")
    forum.grace.post("t2", "CLOSED")
    _ok(forum.grace.push(forum.url, "t2"))


@needs_git
def test_a_second_run_is_refused_while_the_first_is_live(forum):
    forum.ada.open_thread("t1")
    forum.ada.post("t1", "RUN", **_run_post())
    _ok(forum.ada.push(forum.url, "t1"))
    forum.grace.git("fetch", "--quiet", forum.url, f"{forum.ada.ref('t1')}:t1")
    forum.grace.git("switch", "--quiet", "t1")
    # Grace pushes a RUN without having seen... a second one on the same tip.
    forum.grace.post("t1", "RUN", **_run_post(roster={"referee": "other-referee"}))
    _refused(forum.grace.push(forum.url, "t1"), "run is already live")
    # A person's post is accepted at any time.
    forum.grace.git("reset", "--quiet", "--hard", "HEAD~1")
    forum.grace.post("t1", "FINDING")
    _ok(forum.grace.push(forum.url, "t1"))


@needs_git
def test_a_run_is_accepted_once_the_referee_has_ruled(forum):
    forum.ada.open_thread("t1")
    forum.ada.post("t1", "RUN", **_run_post())
    forum.ada.post("t1", "DONE", identity=ROSTER["referee"])
    _ok(forum.ada.push(forum.url, "t1"))
    forum.ada.post("t1", "RUN", **_run_post())
    _ok(forum.ada.push(forum.url, "t1"))


@needs_git
def test_a_blocked_run_is_over_too(forum):
    forum.ada.open_thread("t1")
    forum.ada.post("t1", "RUN", **_run_post())
    forum.ada.post("t1", "BLOCKED", identity=ROSTER["referee"])
    _ok(forum.ada.push(forum.url, "t1"))
    forum.ada.post("t1", "RUN", **_run_post())
    _ok(forum.ada.push(forum.url, "t1"))


@needs_git
def test_a_run_is_accepted_once_it_has_expired_by_the_servers_clock(forum):
    forum.ada.open_thread("t1")
    run = forum.ada.post("t1", "RUN", **_run_post())
    _ok(forum.ada.push(forum.url, "t1"))
    forum.ada.post("t1", "RUN", **_run_post())
    _refused(forum.ada.push(forum.url, "t1"), "run is already live")
    # Nothing arrived for an hour (the posts' own `ts` say nothing of it).
    old = datetime.now(UTC) - timedelta(hours=1)
    for p in forum.live.pushes(forum.id):
        forum.set_received_at(p["post_id"], old)
    _ok(forum.ada.push(forum.url, "t1"))
    assert run["id"] in [p["post_id"] for p in forum.live.pushes(forum.id)]


@needs_git
def test_a_roster_post_keeps_a_run_alive(forum):
    forum.ada.open_thread("t1")
    forum.ada.post("t1", "RUN", **_run_post())
    reply = forum.ada.post("t1", "PROPOSAL", identity=ROSTER["proposer"])
    _ok(forum.ada.push(forum.url, "t1"))
    old = datetime.now(UTC) - timedelta(hours=1)
    for p in forum.live.pushes(forum.id):
        if p["post_id"] != reply["id"]:
            forum.set_received_at(p["post_id"], old)
    forum.ada.post("t1", "RUN", **_run_post())
    _refused(forum.ada.push(forum.url, "t1"), "run is already live")


@needs_git
def test_a_closed_thread_has_no_live_run(forum):
    forum.ada.open_thread("t1")
    forum.ada.post("t1", "RUN", **_run_post())
    forum.ada.post("t1", "CLOSED")
    _ok(forum.ada.push(forum.url, "t1"))
    forum.ada.post("t1", "RUN", **_run_post())
    _ok(forum.ada.push(forum.url, "t1"))


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@needs_git
def test_every_post_is_recorded_with_its_pusher(forum):
    forum.ada.open_thread("t1")
    first = forum.ada.post("t1")
    _ok(forum.ada.push(forum.url, "t1"))
    forum.grace.git("fetch", "--quiet", forum.url, f"{forum.ada.ref('t1')}:t1")
    forum.grace.git("switch", "--quiet", "t1")
    second = forum.grace.post("t1", "RISK")
    _ok(forum.grace.push(forum.url, "t1"))
    rows = forum.live.pushes(forum.id)
    by_post = {r["post_id"]: r for r in rows}
    assert (
        by_post[first["id"]]["pusher_sub"],
        by_post[first["id"]]["pusher_name"],
        by_post[first["id"]]["imported"],
        by_post[first["id"]]["thread_id"],
        by_post[first["id"]]["kind"],
    ) == (str(INSTALL_ID), "Ada", False, "t1", "FINDING")
    assert (
        by_post[second["id"]]["pusher_sub"],
        by_post[second["id"]]["pusher_name"],
    ) == (
        str(OTHER_INSTALL_ID),
        "Grace",
    )
    assert (
        by_post[second["id"]]["commit"]
        == forum.grace.git("rev-parse", "HEAD").stdout.strip()
    )
    kinds = [r["kind"] for r in rows]
    assert kinds == ["TASK", "THREAD", "FINDING", "RISK"] or sorted(kinds) == sorted(
        ["TASK", "THREAD", "FINDING", "RISK"]
    )
    # `after` pages the records.
    seqs = [r["seq"] for r in rows]
    assert seqs == sorted(seqs)
    with forum.live.api() as api:
        later = api.get(f"/forums/{forum.id}/pushes", params={"after": seqs[1]}).json()
    assert [r["seq"] for r in later] == seqs[2:]


@needs_git
def test_a_refused_push_records_nothing(forum):
    forum.ada.open_thread("t1")
    forum.ada.post("t1", "RUN", **_run_post())
    _ok(forum.ada.push(forum.url, "t1"))
    before = forum.live.pushes(forum.id)
    forum.ada.post("t1", "RUN", **_run_post())
    _refused(forum.ada.push(forum.url, "t1"))
    assert forum.live.pushes(forum.id) == before


@needs_git
def test_an_import_creates_threads_and_is_recorded_imported(forum):
    # A moved project's history: a closed thread whose root another account pushed,
    # and a live-looking run, both as they were.
    forum.ada.open_thread("t1")
    forum.ada.post("t1", "RUN", **_run_post())
    forum.ada.post("t1", "RUN", **_run_post())
    forum.ada.post("t1", "CLOSED")
    _ok(forum.ada.push(forum.url, "t1", "vista-import"))
    rows = forum.live.pushes(forum.id)
    assert rows and all(r["imported"] for r in rows)
    assert all(r["pusher_sub"] == str(INSTALL_ID) for r in rows)
    # Without the option, the same history is refused (two RUNs).
    forum.ada.open_thread("t2")
    forum.ada.post("t2", "RUN", **_run_post())
    forum.ada.post("t2", "RUN", **_run_post())
    _refused(forum.ada.push(forum.url, "t2"), "run is already live")


@needs_git
def test_the_import_option_does_not_loosen_an_existing_thread(forum):
    forum.ada.open_thread("t1")
    _ok(forum.ada.push(forum.url, "t1"))
    forum.grace.git("fetch", "--quiet", forum.url, f"{forum.ada.ref('t1')}:t1")
    forum.grace.git("switch", "--quiet", "t1")
    forum.grace.post("t1", "CLOSED")
    _refused(forum.grace.push(forum.url, "t1", "vista-import"), "opener")
    # Nor does it let a push leave the thread refs.
    res = forum.ada.git(
        "push", "-o", "vista-import", forum.url, "t1:refs/heads/main", check=False
    )
    _refused(res, "vista-forum/threads")


@needs_git
def test_a_hook_that_fails_refuses_without_a_traceback(forum, live):
    forum.ada.open_thread("t1")
    _ok(forum.ada.push(forum.url, "t1"))
    forum.ada.post("t1", "RUN", **_run_post())
    # The records are gone: the RUN check cannot read them.
    with live.db() as db:
        db.execute("DROP TABLE forum_push")
    res = forum.ada.push(forum.url, "t1")
    _refused(res, "could not check this push")
    assert "Traceback" not in res.stderr
    log = (live.settings.forums_dir / "forum-hook-errors.log").read_text(
        encoding="utf-8"
    )
    assert forum.id in log and "Traceback" in log


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@needs_git
def test_the_item_knows_its_forum(forum):
    with forum.live.api(OTHER_INSTALL_ID, "Grace") as api:
        res = api.get(f"/items/{forum.item_id}/forum")
        assert res.status_code == 200
        assert (res.json()["id"], res.json()["git_url"]) == (forum.id, forum.url)
    with forum.live.api(THIRD_INSTALL_ID, "Carol") as api:
        assert api.get(f"/items/{forum.item_id}/forum").status_code == 404


@needs_git
def test_a_forum_is_linked_once_by_its_owner(live):
    with live.api() as ada, live.api(OTHER_INSTALL_ID, "Grace") as grace:
        item = lambda name, kind="project": ada.post(  # noqa: E731
            "/items",
            json={"kind": kind, "name": name, "team_id": PUBLIC, "manifest": {}},
        ).json()
        forum = ada.post("/forums").json()
        # Only to a project, and only to one the caller sees.
        assert (
            ada.put(
                f"/forums/{forum['id']}/item",
                json={"item_id": item("s", "skill")["id"]},
            )
        ).status_code == 409
        assert (
            ada.put(f"/forums/{forum['id']}/item", json={"item_id": str(uuid.uuid4())})
        ).status_code == 404
        # Not by someone who cannot see it.
        assert (
            grace.put(f"/forums/{forum['id']}/item", json={"item_id": item("a")["id"]})
        ).status_code == 404
        first = item("p1")
        res = ada.put(f"/forums/{forum['id']}/item", json={"item_id": first["id"]})
        assert res.status_code == 200 and res.json()["item_id"] == first["id"]
        # Once: not again, not to the same item, and not by someone else who now sees it.
        assert (
            ada.put(f"/forums/{forum['id']}/item", json={"item_id": item("p2")["id"]})
        ).status_code == 409
        assert (
            ada.put(f"/forums/{forum['id']}/item", json={"item_id": first["id"]})
        ).status_code == 409
        assert (
            grace.put(f"/forums/{forum['id']}/item", json={"item_id": item("p3")["id"]})
        ).status_code == 403
        # One forum per item.
        other = ada.post("/forums").json()
        assert (
            ada.put(f"/forums/{other['id']}/item", json={"item_id": first["id"]})
        ).status_code == 409


@needs_git
def test_an_unlinked_forum_is_deleted_and_a_linked_one_stays(forum, live):
    with live.api() as ada, live.api(OTHER_INSTALL_ID, "Grace") as grace:
        assert ada.delete(f"/forums/{forum.id}").status_code == 409
        assert grace.delete(f"/forums/{forum.id}").status_code == 403
        assert forums.repo_path(live.settings.forums_dir, uuid.UUID(forum.id)).is_dir()
        spare = ada.post("/forums").json()
        repo = forums.repo_path(live.settings.forums_dir, uuid.UUID(spare["id"]))
        assert repo.is_dir()
        assert grace.delete(f"/forums/{spare['id']}").status_code == 404
        assert ada.delete(f"/forums/{spare['id']}").status_code == 204
        assert not repo.exists()
        assert ada.get(f"/forums/{spare['id']}/pushes").status_code == 404


@needs_git
def test_a_new_repository_is_set_up_for_the_rules(forum, live):
    repo = forums.repo_path(live.settings.forums_dir, uuid.UUID(forum.id))
    config = subprocess.run(
        ["git", "config", "--file", str(repo / "config"), "--list"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    for line in (
        "receive.denynonfastforwards=true",
        "receive.denydeletes=true",
        "receive.advertisepushoptions=true",
    ):
        assert line in config
    for hook in ("pre-receive", "post-receive"):
        assert os.access(repo / "hooks" / hook, os.X_OK)


@needs_git
@pytest.mark.skipif(os.name == "nt", reason="probes a pid with signal 0")
def test_a_marker_left_by_an_exited_push_does_not_stall_the_next(forum, live):
    """
    `post-receive` does not run when `pre-receive` passed but every ref update
    then failed (a lost compare-and-swap), so its marker is never cleared by it.
    """
    gone = subprocess.Popen(["true"])
    gone.wait()
    pending = live.settings.forums_dir / f"{forum.id}.git" / "vista-pending"
    pending.mkdir()
    (pending / str(gone.pid)).touch()
    forum.ada.open_thread("t1")
    started = time.monotonic()
    _ok(forum.ada.push(forum.url, "t1"))
    assert time.monotonic() - started < 5
    assert not (pending / str(gone.pid)).exists()


def test_the_macos_shim_is_never_run_without_developer_tools(monkeypatch):
    shim = "/usr/bin/git"
    ran = []

    def fake_run(args, **kwargs):
        ran.append(args[0])
        return subprocess.CompletedProcess(args, 2, b"", b"")  # xcode-select: none

    monkeypatch.setattr(forums.sys, "platform", "darwin")
    monkeypatch.setattr(forums, "MACOS_GIT_SHIM", Path(shim))
    monkeypatch.setattr(
        forums.shutil,
        "which",
        lambda name: shim if name == "git" else "/usr/bin/xcode-select",
    )
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: self)
    monkeypatch.setattr(forums.subprocess, "run", fake_run)
    assert forums.git_version() is None
    assert not forums.git_usable()
    assert ran == ["/usr/bin/xcode-select"] * 2  # the shim itself was not run


@pytest.mark.anyio
async def test_without_git_forums_answer_503_and_items_work(monkeypatch, tmp_path):
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setattr(forums, "git_version", lambda: None)
    data_dir = tmp_path / "collab"
    settings = CollabSettings(
        data_dir=data_dir,
        database_url=f"sqlite+aiosqlite:///{data_dir / 'collaboration.db'}",
        auth_mode="dev-headers",
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://collab",
            headers=headers(),
        ) as client:
            assert (await client.post("/forums")).status_code == 503
            assert (
                await client.get(f"/git/{uuid.uuid4()}.git/info/refs")
            ).status_code in (404, 503)
            res = await client.post(
                "/items",
                json={"kind": "skill", "name": "s", "team_id": PUBLIC, "manifest": {}},
            )
            assert res.status_code == 201
            assert (await client.get("/me")).status_code == 200
