"""
Fake VISTA releases for the in-app update's manual end-to-end run
(docs/validation-lane.md, "In-app update").

    python3 fake_release.py make <built archive> <version> <releases dir>
    python3 fake_release.py serve <releases dir> [port]

`make` writes <releases dir>/v<version>/ with what a real release carries for
the archive's platform: the archive restamped as <version> (its top folder,
VERSION and manifest.json; nothing inside the window changes, so a macOS
signature still holds), its .sha256, the rendered installers, an update.json
that keeps data from 0.0.1 on, and a vista-schema.sqlite.

`serve` stands in for api.github.com at http://127.0.0.1:<port> (default
8765): releases/latest is the newest folder, releases?per_page=20 lists them
all, and each asset carries the sha256 digest GitHub reports. Point
VISTA_BACKEND_UPDATES__GITHUB_API at it. Folders are read on every request, so
a release can be added while it runs.

Standard library only, so any Python 3.9+ runs it.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
NAME = re.compile(
    r"^vista-(?P<version>.+)-(?P<platform>mac-arm64|linux-x86|win-x86)\.(?P<ext>tar\.gz|zip)$"
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# make
# ---------------------------------------------------------------------------


def restamp(folder: Path, version: str) -> None:
    (folder / "VERSION").write_text(version + "\n", encoding="utf-8")
    manifest = folder / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["version"] = version
    manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def make(archive: Path, version: str, releases: Path) -> None:
    found = NAME.match(archive.name)
    if not found:
        raise SystemExit(f"not a VISTA package archive: {archive.name}")
    platform, ext = found["platform"], found["ext"]
    name = f"vista-{version}-{platform}"
    out = releases / f"v{version}"
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{name}.{ext}"

    with tempfile.TemporaryDirectory() as stage_dir:
        stage = Path(stage_dir)
        if ext == "tar.gz":
            subprocess.run(["tar", "-xf", str(archive), "-C", str(stage)], check=True)
        else:
            with zipfile.ZipFile(archive) as z:
                z.extractall(stage)
        (built,) = [p for p in stage.iterdir() if p.is_dir()]
        if built.name != name:
            built = built.rename(stage / name)
        restamp(built, version)
        if ext == "tar.gz":
            # The system tar keeps the app bundle's symlinks and modes exactly.
            subprocess.run(
                ["tar", "-czf", str(target), "-C", str(stage), name], check=True
            )
        else:
            shutil.make_archive(str(out / name), "zip", stage, name)

    (out / f"{target.name}.sha256").write_text(
        f"{sha256(target)}  {target.name}\n", encoding="utf-8"
    )
    subprocess.run(
        ["bash", str(REPO_ROOT / ".github/scripts/render-installers.sh"), str(out)],
        check=True,
        env={
            **os.environ,
            "VERSION": version,
            "REPO_URL": "https://github.com/Genesis-VISTA/vista",
        },
    )
    (out / "update.json").write_text(
        '{"data_compatible_from": "0.0.1"}\n', encoding="utf-8"
    )
    # An empty database stands in: the backend only downloads it, never reads it.
    (out / "vista-schema.sqlite").write_bytes(b"")
    for f in sorted(out.iterdir()):
        print(f"{f.stat().st_size:>12}  {f.name}")


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


_digests: dict = {}


def digest(path: Path) -> str:
    key = (path, path.stat().st_mtime, path.stat().st_size)
    if key not in _digests:
        _digests[key] = sha256(path)
    return _digests[key]


def version_key(tag: str):
    return tuple(int(p) for p in tag.lstrip("v").split("-")[0].split("."))


def serve(releases: Path, port: int) -> None:
    repo = "/repos/Genesis-VISTA/vista"

    def release(folder: Path) -> dict:
        tag = folder.name
        return {
            "tag_name": tag,
            "draft": False,
            "prerelease": "-" in tag,
            "html_url": f"http://127.0.0.1:{port}/notes/{tag}",
            "assets": [
                {
                    "name": f.name,
                    "size": f.stat().st_size,
                    "digest": "sha256:" + digest(f),
                    "browser_download_url": f"http://127.0.0.1:{port}/files/{tag}/{f.name}",
                }
                for f in sorted(folder.iterdir())
                if f.is_file() and not f.name.startswith(".")
            ],
        }

    def listed() -> list:
        folders = [
            d for d in releases.iterdir() if d.is_dir() and d.name.startswith("v")
        ]
        return [
            release(d)
            for d in sorted(folders, key=lambda d: version_key(d.name), reverse=True)
        ]

    class Handler(BaseHTTPRequestHandler):
        def _json(self, body, status=200):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == f"{repo}/releases/latest":
                stable = [r for r in listed() if not r["prerelease"]]
                return self._json(
                    stable[0] if stable else {"message": "Not Found"},
                    200 if stable else 404,
                )
            if path == f"{repo}/releases":
                return self._json(listed())
            if path.startswith("/files/"):
                _, _, tag, name = path.split("/", 3)
                f = releases / tag / name
                if not f.is_file():
                    return self._json({"message": "Not Found"}, 404)
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(f.stat().st_size))
                self.end_headers()
                with f.open("rb") as src:
                    shutil.copyfileobj(src, self.wfile, 1 << 20)
                return None
            return self._json({"message": "Not Found"}, 404)

        def log_message(self, fmt, *args):
            sys.stderr.write("fake-github: " + fmt % args + "\n")

    print(f"fake GitHub on http://127.0.0.1:{port} serving {releases}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main(argv: list) -> None:
    if len(argv) == 4 and argv[0] == "make":
        make(Path(argv[1]).resolve(), argv[2], Path(argv[3]).resolve())
    elif len(argv) in (2, 3) and argv[0] == "serve":
        serve(Path(argv[1]).resolve(), int(argv[2]) if len(argv) == 3 else 8765)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
