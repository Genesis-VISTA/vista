"""
Downloading a release for "Update and restart" (openspec change `in-app-updates`).

Asked for only when the researcher clicks Update. The release's archive for
this platform, its `.sha256` and its installer (plus `update.json` when the
release has one) are streamed into `VISTA_HOME/updates/<version>/` while VISTA
keeps running. Each is checked against the sha256 digest the GitHub API
reports for it, and the archive against its `.sha256` as well; a file that
fails is deleted and the download stops with the reason. An asset GitHub
reports no digest for is refused, never trusted. When everything matches,
`ready.json` records the version and each file's hash, which the window
checks again before it hands the cache to the installer.

The cache is removed by the next start of the version it holds (the update
worked), or replaced by a newer download.
"""

import asyncio
import hashlib
import json
import logging
import platform as host_platform
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

from ..config import settings
from .update_check import Release, parse_version

log = logging.getLogger(__name__)

INSTALL_FLOOR = 7 * 1024**3
""" The free space the installer insists on (`VISTA_INSTALL_MIN_FREE_GB`, 7 GB). """

CHUNK = 1024 * 1024


class DownloadFailed(Exception):
    """A reason the update cannot go ahead, worded for the researcher."""


def cache_root() -> Path:
    """Where downloads go: `updates/` in VISTA's state directory."""
    return settings.data_dir / "updates"


def platform_name(
    system: str = sys.platform, machine: str = host_platform.machine()
) -> str | None:
    """This machine as the release names its archives, or `None` if no release is built for it."""
    machine = machine.lower()
    if system == "darwin" and machine == "arm64":
        return "mac-arm64"
    if system.startswith("linux") and machine in ("x86_64", "amd64"):
        return "linux-x86"
    if system == "win32" and machine in ("x86_64", "amd64"):
        return "win-x86"
    return None


def required_assets(version: str, platform: str) -> tuple[str, str, str]:
    """The archive, its `.sha256` and the installer, by the names the release gives them."""
    windows = platform.startswith("win")
    archive = f"vista-{version}-{platform}.{'zip' if windows else 'tar.gz'}"
    return archive, f"{archive}.sha256", "install.ps1" if windows else "install.sh"


def _digest(asset: dict[str, Any]) -> str | None:
    """The asset's `sha256:<hex>` digest from the API, as lowercase hex."""
    digest = asset.get("digest")
    if isinstance(digest, str) and digest.lower().startswith("sha256:"):
        return digest.split(":", 1)[1].strip().lower()
    return None


def _gb(n: int) -> str:
    return f"{n / 1024**3:.1f}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def read_ready(folder: Path) -> dict[str, Any] | None:
    """A folder's `ready.json` (`{"version", "files": {name: sha256}}`), if it has a readable one."""
    try:
        ready = json.loads((folder / "ready.json").read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    if not isinstance(ready, dict) or not isinstance(ready.get("files"), dict):
        return None
    return ready


def _still_ready(folder: Path, version: str) -> bool:
    """Whether an earlier download of `version` is complete and unchanged."""
    ready = read_ready(folder)
    if ready is None or ready.get("version") != version:
        return False
    return all(
        (folder / name).is_file() and _sha256_file(folder / name) == expected
        for name, expected in ready["files"].items()
    )


def clean_cache(installed: str, root: Path | None = None) -> None:
    """
    On start: remove every cached download that is not newer than the running
    version. The one equal to it is the update that just worked. Leaves
    `last-failure.json` and anything else that is not a version's folder.
    """
    root = root or cache_root()
    if not root.is_dir():
        return
    running = parse_version(installed)
    for child in root.iterdir():
        if not child.is_dir():
            continue
        cached = parse_version(child.name)
        if child.name == installed or (
            running is not None and cached is not None and cached <= running
        ):
            shutil.rmtree(child, ignore_errors=True)
            log.info("Removed the update cache for %s.", child.name)


@dataclass
class DownloadStatus:
    state: Literal["idle", "downloading", "ready", "failed"] = "idle"
    version: str | None = None
    bytes: int = 0
    total: int = 0
    error: str | None = None


@dataclass
class UpdateDownloader:
    """One download at a time, in the background. One per process (`update_downloader`)."""

    root: Callable[[], Path] = cache_root
    transport: httpx.AsyncBaseTransport | None = None
    """ Tests serve their own release; `None` is the network. """
    free_bytes: Callable[[Path], int] = lambda path: shutil.disk_usage(path).free
    platform: str | None = None
    """ `None` is this machine (`platform_name`). """
    status: DownloadStatus = field(default_factory=DownloadStatus)
    _task: asyncio.Task | None = None

    def start(self, release: Release) -> DownloadStatus:
        """Start downloading `release`, unless it is already downloading or ready."""
        if self.status.version == release.version and self.status.state in (
            "downloading",
            "ready",
        ):
            return self.status
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self.status = DownloadStatus(state="downloading", version=release.version)
        self._task = asyncio.create_task(self._run(release))
        return self.status

    async def wait(self) -> None:
        """Until the current download has finished, one way or the other."""
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        await self.wait()

    def ready_folder(self, version: str) -> Path | None:
        """The verified download of `version`, if there is one."""
        if self.status.state == "ready" and self.status.version == version:
            return self.root() / version
        return None

    async def _run(self, release: Release) -> None:
        folder = self.root() / release.version
        try:
            await self._download(release, folder)
        except asyncio.CancelledError:
            _remove_parts(folder)
            raise
        except DownloadFailed as exc:
            self._fail(folder, str(exc))
        except Exception as exc:  # noqa: BLE001 - a download never breaks VISTA
            log.exception("Downloading VISTA %s failed", release.version)
            self._fail(folder, f"The download failed: {exc}")

    def _fail(self, folder: Path, reason: str) -> None:
        _remove_parts(folder)
        (folder / "ready.json").unlink(missing_ok=True)
        log.warning("Update download failed: %s", reason)
        self.status.state = "failed"
        self.status.error = reason

    async def _download(self, release: Release, folder: Path) -> None:
        platform = self.platform or platform_name()
        if platform is None:
            raise DownloadFailed("No VISTA release is built for this machine.")
        wanted: list[tuple[str, dict[str, Any], str]] = []
        for name in required_assets(release.version, platform):
            asset = release.asset(name)
            if asset is None:
                raise DownloadFailed(f"The {release.version} release has no {name}.")
            digest = _digest(asset)
            if digest is None:
                raise DownloadFailed(
                    f"GitHub reports no digest for {name}, so it cannot be verified."
                )
            wanted.append((name, asset, digest))
        extra = release.asset("update.json")
        if extra is not None and (digest := _digest(extra)) is not None:
            wanted.append(("update.json", extra, digest))

        root = self.root()
        root.mkdir(parents=True, exist_ok=True)
        # Only one download is kept: a newer one replaces it.
        for child in root.iterdir():
            if child.is_dir() and child.name != release.version:
                shutil.rmtree(child, ignore_errors=True)

        if await asyncio.to_thread(_still_ready, folder, release.version):
            self.status.state = "ready"
            return

        total = sum(int(a.get("size") or 0) for _, a, _ in wanted)
        need, free = total + INSTALL_FLOOR, self.free_bytes(root)
        if free < need:
            raise DownloadFailed(
                f"Updating needs about {_gb(need)} GB free in {root}, and there is "
                f"{_gb(free)} GB. Free some space and try again."
            )
        folder.mkdir(exist_ok=True)
        (folder / "ready.json").unlink(missing_ok=True)
        self.status.total = total

        hashes: dict[str, str] = {}
        async with httpx.AsyncClient(
            transport=self.transport,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, read=120.0),
            headers={"User-Agent": f"VISTA/{settings.version}"},
        ) as client:
            for name, asset, digest in wanted:
                url = asset.get("browser_download_url")
                if not isinstance(url, str):
                    raise DownloadFailed(
                        f"GitHub gives no download address for {name}."
                    )
                hashes[name] = await self._fetch(client, url, folder / name, digest)

        archive, checksum, _ = required_assets(release.version, platform)
        listed = (folder / checksum).read_text(encoding="utf-8").split()
        if not listed or listed[0].lower() != hashes[archive]:
            (folder / archive).unlink(missing_ok=True)
            raise DownloadFailed(
                f"{archive} does not match its .sha256 file, so it was deleted."
            )

        (folder / "ready.json").write_text(
            json.dumps({"version": release.version, "files": hashes}, indent=2),
            encoding="utf-8",
        )
        self.status.state = "ready"
        log.info("VISTA %s is downloaded and verified in %s.", release.version, folder)

    async def _fetch(
        self, client: httpx.AsyncClient, url: str, target: Path, digest: str
    ) -> str:
        """Stream `url` to `target`, returning its sha256 once it matches `digest`."""
        part = target.with_name(target.name + ".part")
        hasher = hashlib.sha256()
        try:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise DownloadFailed(
                        f"Downloading {target.name} failed: HTTP {response.status_code}."
                    )
                with part.open("wb") as out:
                    async for chunk in response.aiter_bytes(CHUNK):
                        out.write(chunk)
                        hasher.update(chunk)
                        self.status.bytes += len(chunk)
        except httpx.HTTPError as exc:
            raise DownloadFailed(f"Downloading {target.name} failed: {exc}") from exc
        got = hasher.hexdigest()
        if got != digest:
            part.unlink(missing_ok=True)
            raise DownloadFailed(
                f"{target.name} failed verification: its sha256 differs from the "
                "digest GitHub reports, so it was deleted."
            )
        part.replace(target)
        return got


def _remove_parts(folder: Path) -> None:
    if folder.is_dir():
        for part in folder.glob("*.part"):
            part.unlink(missing_ok=True)


update_downloader = UpdateDownloader()
"""The process's downloader: started by `POST /updates/download`."""
