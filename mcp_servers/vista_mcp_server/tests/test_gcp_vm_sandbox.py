"""The microVM, really booted.

Marked `sandbox`, so the hermetic filter `not live and not hpc and not sandbox`
excludes it: it needs the `vista-globus` image loaded and hardware
virtualisation, neither of which a PR runner has.

It exists because the mount arguments are the one part of this that unit tests
cannot settle. `test_gcp_vm.py` asserts what argv VISTA builds; only a booted
guest shows what the runtime does with it. A mount that arrived at the wrong
path, or read-write where it was asked to be read-only, is invisible to an argv
assertion and would be discovered as a transfer that silently found nothing.
"""

import subprocess

import pytest

from vista_mcp_server.lib import gcp_vm
from vista_mcp_server.lib.gcp_vm import Endpoint

pytestmark = pytest.mark.sandbox


@pytest.fixture(scope="module")
def msb():
    found = gcp_vm.msb_path()
    if found is None:
        pytest.skip("no microsandbox binary; set VISTA_MSB_PATH")
    image = subprocess.run(
        [str(found), "image", "inspect", "--format=json", gcp_vm.IMAGE],
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )
    if image.returncode != 0:
        pytest.skip(
            f"the {gcp_vm.IMAGE} image is not loaded; build the package or "
            f"`msb load` it first"
        )
    return found


@pytest.fixture
def endpoint(tmp_path, msb):
    """A booted microVM for one test, torn down however the test ends."""
    # Siblings, as the packaged layout has them: the jobs directory is not
    # inside the data directory, so both have to be mounted.
    data_dir = tmp_path / "state"
    hpc_jobs_dir = tmp_path / "hpc_jobs"
    endpoint = Endpoint(data_dir=data_dir, hpc_jobs_dir=hpc_jobs_dir)
    endpoint.ensure_directories()
    endpoint.create_vm()
    try:
        yield endpoint
    finally:
        endpoint.stop()


def run_in_guest(endpoint, script):
    """Run a shell snippet inside the guest, whether or not it succeeds."""
    return subprocess.run(
        endpoint.exec_argv(gcp_vm.msb_path(), ["sh", "-c", script], tty=False),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=120,
    )


def in_guest(endpoint, script):
    """Run a shell snippet inside the guest and return what it printed."""
    result = run_in_guest(endpoint, script)
    assert result.returncode == 0, result.stderr.decode()
    return result.stdout.decode()


def test_both_directories_are_visible_at_their_host_paths(endpoint):
    """Every absolute path the MCP server hands a transfer is a host path, so a
    mount whose destination differs from its source resolves to nothing."""
    marker = endpoint.volumes_dir / "written-on-the-host.txt"
    marker.write_text("hello")
    (endpoint.hpc_jobs_dir / "job.sbatch").write_text("#!/bin/sh\n")

    assert "hello" in in_guest(endpoint, f"cat {marker}")
    assert in_guest(endpoint, f"ls {endpoint.hpc_jobs_dir}").strip() == "job.sbatch"


def test_each_mount_arrives_with_the_access_it_was_given(endpoint):
    """Asserted against the guest's own mount table.

    Checking behaviour instead would not distinguish the two failures that
    matter: a write refused because the mount is read-only and a write refused
    because the mount is not there at all report the same thing.
    """
    mounts = in_guest(endpoint, "cat /proc/mounts")
    by_path = {
        fields[1]: fields[3].split(",")
        for fields in (line.split() for line in mounts.splitlines())
        if len(fields) > 3
    }

    for mount in endpoint.mounts():
        options = by_path.get(str(mount.path))
        assert options is not None, f"{mount.path} is not mounted: {mounts}"
        assert ("ro" in options) == mount.readonly, (mount, options)


def test_the_jobs_directory_refuses_writes(endpoint):
    """A boundary the kernel enforces, inside the one `-restrict-paths` asks
    Globus Connect Personal to respect. Nothing needs to write here: job sources
    are read out of it, and outputs come back to `volumes/`."""
    refused = run_in_guest(endpoint, f"touch {endpoint.hpc_jobs_dir}/sneaky")

    assert refused.returncode != 0
    assert "Read-only file system" in refused.stderr.decode()
    assert not (endpoint.hpc_jobs_dir / "sneaky").exists()


def test_the_data_directory_is_writable_from_the_guest(endpoint):
    """microsandbox's bind identity map rewrites the host owner to the guest
    user, so an ordinary host directory is writable with no mount options and no
    widening of host permissions. Downloads and fetched logs land here."""
    in_guest(endpoint, f"touch {endpoint.volumes_dir}/from-guest.txt")

    written = endpoint.volumes_dir / "from-guest.txt"
    assert written.exists()
    assert written.stat().st_uid == endpoint.volumes_dir.stat().st_uid


def test_the_repository_root_is_not_mounted(endpoint, tmp_path):
    """What stops the transfer endpoint reading `.env` and both deployment
    refresh tokens. The container this replaces mounted the repository, because
    the launch script re-executed itself inside; nothing of VISTA's runs in the
    guest now."""
    repo_root = str(gcp_vm.Path(__file__).resolve().parents[3])

    assert (
        in_guest(
            endpoint, f"test -e {repo_root} && echo present || echo absent"
        ).strip()
        == "absent"
    )


def test_the_guest_has_the_interpreter_globus_needs(endpoint):
    """Globus Connect Personal's `_gcp_invokepython` shim searches for a system
    python3 and exits without one."""
    assert "Python 3" in in_guest(endpoint, "python3 --version 2>&1")


def test_the_guest_user_is_not_root(endpoint):
    """Globus Connect Personal refuses to run as root."""
    assert in_guest(endpoint, "id -un").strip() == gcp_vm.GUEST_USER
