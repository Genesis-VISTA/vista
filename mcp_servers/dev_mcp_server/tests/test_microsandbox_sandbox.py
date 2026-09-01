import io
import json
import tarfile
from pathlib import Path

import pytest

from dev_mcp_server.lib.microsandbox_sandbox import MicrosandboxSandbox


def write_image_tar(path: Path, config: str | None) -> Path:
    """
    A minimal image archive whose `manifest.json` names `config` as the config
    blob — enough for the digest read, which never touches layer data. `config`
    of None writes an archive with no manifest at all.
    """
    with tarfile.open(path, "w") as archive:
        if config is not None:
            manifest = json.dumps([{"Config": config, "Layers": []}]).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(manifest)
            archive.addfile(info, io.BytesIO(manifest))
    return path


class _AwaitableValue:
    def __init__(self, value: str) -> None:
        self._value = value

    def __await__(self):
        async def _resolve():
            return self._value

        return _resolve().__await__()


class _FakeSandbox:
    def __init__(self) -> None:
        self.name = _AwaitableValue("vista-sandbox-test")
        self.stop_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1


class TestMicrosandboxClose:
    @pytest.fixture
    def anyio_backend(self):
        return "asyncio"

    @pytest.mark.anyio
    async def test_close_stops_and_removes_sandbox(self, monkeypatch):
        removed: list[str] = []

        async def fake_remove(name: str) -> None:
            removed.append(name)

        monkeypatch.setattr(
            "dev_mcp_server.lib.microsandbox_sandbox.MsbSandbox.remove",
            fake_remove,
        )

        sandbox_impl = _FakeSandbox()
        sandbox = MicrosandboxSandbox(sandbox_impl)

        await sandbox.close()

        assert sandbox_impl.stop_calls == 1
        assert removed == ["vista-sandbox-test"]


class TestMicrosandboxBuild:
    """
    `_build` picks one of three ways to get the sandbox image into
    microsandbox's store. The oci_image_tar path is the one that needs no
    container runtime, so it is what makes running VISTA inside a container
    possible — a regression there presents as a failed *first agent turn* on a
    deployment that looked healthy, so it is pinned here rather than trusted.
    """

    @pytest.fixture
    def anyio_backend(self):
        return "asyncio"

    @pytest.fixture
    def calls(self, monkeypatch):
        """Record what `_build` shells out to, and report an empty image store."""
        recorded: list[tuple[str, ...]] = []

        async def fake_check_output(*args: str, **kwargs) -> str:
            recorded.append(args)
            return ""

        async def fake_parse_output(*args: str, **kwargs):
            recorded.append(args)
            return None  # no such image in the store

        monkeypatch.setattr(
            "dev_mcp_server.lib.microsandbox_sandbox.check_output", fake_check_output
        )
        monkeypatch.setattr(
            "dev_mcp_server.lib.microsandbox_sandbox.parse_output", fake_parse_output
        )
        return recorded

    @pytest.mark.anyio
    async def test_oci_tar_is_loaded_without_a_container_runtime(self, calls, tmp_path):
        tar = write_image_tar(tmp_path / "vista-sandbox.tar", "abc.json")

        image = await MicrosandboxSandbox._build(oci_image_tar=tar, image="vista:test")

        assert image == "vista:test"
        loads = [c for c in calls if "load" in c]
        assert loads, f"expected an `msb load`, got {calls}"
        assert loads[0][1:] == ("load", "-i", str(tar.resolve()), "-t", "vista:test")
        # The point of this path: nothing was built, saved, or pulled.
        assert not [c for c in calls if {"build", "save", "pull"} & set(c)], calls

    @pytest.fixture
    def present(self, monkeypatch):
        """
        Report `stored` as the digest microsandbox already holds, and record what
        `_build` shells out to. Returns the recording list.
        """

        def install(stored: str) -> list[tuple[str, ...]]:
            async def already_there(*args: str, **kwargs):
                return {"config": {"digest": f"sha256:{stored}"}}

            recorded: list[tuple[str, ...]] = []

            async def fake_check_output(*args: str, **kwargs) -> str:
                recorded.append(args)
                return ""

            monkeypatch.setattr(
                "dev_mcp_server.lib.microsandbox_sandbox.parse_output", already_there
            )
            monkeypatch.setattr(
                "dev_mcp_server.lib.microsandbox_sandbox.check_output",
                fake_check_output,
            )
            return recorded

        return install

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        "config",
        [
            pytest.param("abc.json", id="docker-archive"),
            pytest.param("blobs/sha256/abc", id="oci-layout"),
        ],
    )
    async def test_oci_tar_is_skipped_when_the_archive_digest_matches(
        self, present, tmp_path, config
    ):
        """Both archive layouts name the config blob by its digest."""
        recorded = present("abc")
        tar = write_image_tar(tmp_path / "vista-sandbox.tar", config)

        await MicrosandboxSandbox._build(oci_image_tar=tar, image="vista:test")

        assert recorded == [], "an unchanged image must not be reloaded on every spawn"

    @pytest.mark.anyio
    async def test_oci_tar_is_reloaded_when_the_stored_image_is_stale(
        self, present, tmp_path
    ):
        """
        The tag is fixed and microsandbox's store outlives the container, so a
        rebuilt tar arriving in a new server image is indistinguishable from the
        old one by presence alone. Getting this wrong is silent: every microVM
        keeps running the previous sandbox image, with nothing in the logs.
        """
        recorded = present("stale")
        tar = write_image_tar(tmp_path / "vista-sandbox.tar", "fresh.json")

        await MicrosandboxSandbox._build(oci_image_tar=tar, image="vista:test")

        loads = [c for c in recorded if "load" in c]
        assert loads, f"expected a reload for the new digest, got {recorded}"
        assert loads[0][1:] == ("load", "-i", str(tar.resolve()), "-t", "vista:test")

    @pytest.mark.anyio
    async def test_archive_without_a_manifest_is_refused(self, present, tmp_path):
        """
        The tar is the sandbox image, so one that cannot be read is fatal. Saying
        so beats loading it anyway and failing further in, or leaving whatever
        the store already holds in place.
        """
        present("abc")
        tar = write_image_tar(tmp_path / "vista-sandbox.tar", None)

        with pytest.raises(ValueError, match="not an image archive"):
            await MicrosandboxSandbox._build(oci_image_tar=tar, image="vista:test")

    @pytest.mark.anyio
    async def test_a_tar_that_is_not_a_tar_is_refused(self, present, tmp_path):
        present("abc")
        tar = tmp_path / "vista-sandbox.tar"
        tar.write_bytes(b"not a tar at all")

        with pytest.raises(tarfile.TarError):
            await MicrosandboxSandbox._build(oci_image_tar=tar, image="vista:test")

    @pytest.mark.anyio
    async def test_dockerfile_and_oci_tar_together_are_refused(self, tmp_path):
        with pytest.raises(ValueError, match="both dockerfile and oci_image_tar"):
            await MicrosandboxSandbox._build(
                dockerfile=tmp_path / "Dockerfile", oci_image_tar=tmp_path / "x.tar"
            )

    @pytest.mark.anyio
    async def test_no_source_at_all_is_refused(self):
        with pytest.raises(ValueError, match="image, dockerfile or oci_image_tar"):
            await MicrosandboxSandbox._build()
