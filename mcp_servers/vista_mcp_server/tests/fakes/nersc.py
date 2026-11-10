"""
NERSC's IRI v1 service for Perlmutter, over an in-memory filesystem.

An `httpx.MockTransport` handler that answers the way the live service was
recorded answering (`tests/fixtures/iri/nersc_v1/`, whose README says how):
every filesystem operation is a task polled at `/api/v1/task/{id}`, `ls` names
entries by their resolved `/global/u1/...` path with the folder itself first,
`download` returns text as text and anything else as base64, and a missing path
is a failed task whose error says 404. The real `IriClient` talks to it.
"""

from __future__ import annotations

import base64
import json
import posixpath
from typing import Any

import httpx

HOMES = "/global/homes/"
RESOLVED = "/global/u1/"


class FakeNersc:
    def __init__(self, files: dict[str, bytes] | None = None):
        self.files: dict[str, bytes] = dict(files or {})
        self.dirs: set[str] = set()
        for path in self.files:
            self._add_parents(path)
        self.job_status: dict[str, Any] = {"state": "active"}
        self.submitted: list[dict] = []
        self.requests: list[httpx.Request] = []
        self.fail: dict[tuple[str, str], str] = {}
        """ (op, path) -> the error a task for it fails with. """
        self.stall: set[str] = set()
        """ Operations whose tasks never finish. """
        self._tasks: dict[str, dict] = {}

    # ------------------------------------------------------------- filesystem

    def _add_parents(self, path: str) -> None:
        d = posixpath.dirname(path)
        while d not in ("", "/"):
            self.dirs.add(d)
            d = posixpath.dirname(d)

    def _exists(self, path: str) -> bool:
        return path in self.files or path in self.dirs

    @staticmethod
    def _resolved(path: str) -> str:
        return path.replace(HOMES, RESOLVED, 1)

    def _entry(self, path: str) -> dict:
        is_dir = path in self.dirs
        return {
            "name": self._resolved(path),
            "type": "d" if is_dir else "f",
            "size": "512" if is_dir else str(len(self.files[path])),
        }

    def _run(self, op: str, path: str, args: dict, upload: bytes | None) -> Any:
        if op == "mkdir":
            self.dirs.add(path)
            self._add_parents(path + "/x")
            return None
        if op == "upload":
            if not self._exists(posixpath.dirname(path)):
                raise FileNotFoundError(path)
            self.files[path] = upload or b""
            return f"Uploaded {path}"
        if not self._exists(path):
            raise FileNotFoundError(path)
        if op == "ls":
            recursive = str(args.get("recursive", "false")).lower() == "true"
            below = [
                p
                for p in sorted(self.dirs | set(self.files))
                if p.startswith(path + "/")
                and (recursive or "/" not in p[len(path) + 1 :])
            ]
            return [self._entry(path)] + [self._entry(p) for p in below]
        if op == "head":
            lines = int(args.get("lines") or 10)
            text = self.files[path].decode("utf-8")
            return {"content": "".join(text.splitlines(keepends=True)[:lines])}
        if op == "stat":
            return {"size": len(self.files[path])}
        if op == "download":
            data = self.files[path]
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                return base64.b64encode(data).decode("ascii")
        raise AssertionError(f"unexpected op {op}")

    def _task(self, op: str, path: str, args: dict, upload: bytes | None) -> dict:
        command = {
            "router": "filesystem",
            "command": op,
            "args": {"path": path, **args},
        }
        if op in self.stall:
            return {"status": "active", "result": None, "command": command}
        if (op, path) in self.fail:
            return {
                "status": "failed",
                "result": {"error": self.fail[(op, path)]},
                "command": command,
            }
        try:
            output = self._run(op, path, args, upload)
        except FileNotFoundError:
            error = f"Exception: Error: 404: {op}: '{self._resolved(path)}': No such file or directory"
            return {"status": "failed", "result": {"error": error}, "command": command}
        return {"status": "completed", "result": {"output": output}, "command": command}

    # ------------------------------------------------------------- http

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        parts = request.url.path.split("/")  # "", "api", "v1", ...
        if parts[3] == "task":
            task_id = parts[4]
            return httpx.Response(200, json={"id": task_id, **self._tasks[task_id]})
        if parts[3] == "filesystem":
            op = parts[4]
            params = dict(request.url.params)
            upload = None
            if op == "upload":
                upload = request.content.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[
                    0
                ]
                args = {}
            elif request.method == "POST":
                args = json.loads(request.content)
            else:
                args = params
            path = params.get("path") or args.pop("path")
            args.pop("path", None)
            task_id = f"t{len(self._tasks)}"
            self._tasks[task_id] = self._task(op, path, args, upload)
            return httpx.Response(
                200, json={"task_id": task_id, "task_uri": f"/api/v1/task/{task_id}"}
            )
        if parts[3] == "compute" and parts[4] == "job":
            self.submitted.append(json.loads(request.content))
            return httpx.Response(
                200, json={"id": "1001", "status": {"state": "queued"}}
            )
        if parts[3] == "compute" and parts[4] == "status":
            return httpx.Response(200, json={"id": parts[6], "status": self.job_status})
        return httpx.Response(404, json={"detail": "not found"})
