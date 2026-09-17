"""Tests for the Globus Connect Personal microVM.

Everything here drives a fake `msb` -- a shell script that records its argument
list and answers from environment variables. No microVM is created, so these
run under the hermetic filter. The test that really boots one is marked
`sandbox` and lives alongside.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

from vista_mcp_server.lib import gcp_vm
from vista_mcp_server.lib.gcp_vm import Endpoint, State

pytestmark = pytest.mark.unit

FAKE_MSB = """#!/bin/sh
echo "$@" >> "$FAKE_MSB_LOG"
case "$1 $2" in
  "image inspect")
    [ "${FAKE_IMAGE:-present}" = present ] || { echo "error: image not found" >&2; exit 1; }
    echo '{"architecture":"arm64"}'; exit 0 ;;
esac
case "$1" in
  status)
    [ "${FAKE_VM:-none}" = none ] && { echo "error: sandbox not found" >&2; exit 1; }
    printf '{"name":"vista-globus","status":"%s"}\\n' "$FAKE_VM"; exit 0 ;;
  create|stop|remove|exec) exit 0 ;;
esac
exit 0
"""


@pytest.fixture
def fake_msb(tmp_path, monkeypatch):
    """A stand-in `msb` on VISTA_MSB_PATH, plus the log of how it was called."""
    script = tmp_path / "msb"
    script.write_text(FAKE_MSB)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "msb.log"
    log.touch()
    monkeypatch.setenv("VISTA_MSB_PATH", str(script))
    monkeypatch.setenv("FAKE_MSB_LOG", str(log))
    # Pinned so the tests assert the microVM path on a Linux runner too.
    monkeypatch.setattr(gcp_vm, "uses_microvm", lambda: True)
    return log


@pytest.fixture(autouse=True)
def no_downloads(monkeypatch):
    """No test here may reach the network. Without this, anything that calls
    `resolve_gcp` on a data directory with no install downloads the real Globus
    Connect Personal tarball -- which these tests did, at fourteen seconds a go,
    until it was noticed."""

    def refuse(*args, **kwargs):
        raise AssertionError("a unit test tried to download Globus Connect Personal")

    monkeypatch.setattr(gcp_vm.urllib.request, "urlopen", refuse)


@pytest.fixture
def installed_gcp(endpoint):
    """Globus Connect Personal already installed under the data directory."""
    gcp = endpoint.data_dir / "globusconnectpersonal" / "globusconnectpersonal"
    gcp.parent.mkdir(parents=True, exist_ok=True)
    gcp.write_text("#!/bin/sh\n")
    return gcp


@pytest.fixture
def repo_root(tmp_path):
    """A checkout-shaped tree: data/ and hpc_jobs/ side by side under a root."""
    root = tmp_path / "vista"
    (root / "data" / "volumes").mkdir(parents=True)
    (root / "hpc_jobs").mkdir(parents=True)
    return root


@pytest.fixture
def endpoint(repo_root):
    return Endpoint(repo_root / "data", repo_root / "hpc_jobs")


def set_up(endpoint):
    """Make the endpoint look like one whose collection already exists."""
    endpoint.client_id_file.parent.mkdir(parents=True, exist_ok=True)
    endpoint.client_id_file.write_text("fake-collection-id\n")
    return endpoint


class TestCreateArguments:
    def test_the_guest_is_not_root(self, endpoint):
        argv = endpoint.create_argv("/msb", ("10.0.0.1",))
        assert "--user" in argv
        assert argv[argv.index("--user") + 1] == "ubuntu"

    def test_memory_and_image_are_pinned(self, endpoint):
        argv = endpoint.create_argv("/msb", ("10.0.0.1",))
        assert argv[argv.index("--memory") + 1] == "512M"
        assert gcp_vm.IMAGE in argv

    def test_the_image_is_never_pulled(self, endpoint):
        argv = endpoint.create_argv("/msb", ("10.0.0.1",))
        assert argv[argv.index("--pull") + 1] == "never"

    def test_no_dockerfile_is_ever_passed(self, endpoint):
        """The microsandbox backend reads a dockerfile as "build this with
        docker or podman first", which is the dependency being removed."""
        argv = endpoint.create_argv("/msb", ("10.0.0.1",))
        assert not any(
            arg.startswith("--") and "dockerfile" in arg.lower() for arg in argv
        )
        assert not any(Path(arg).name.lower().startswith("dockerfile") for arg in argv)
        assert "--build" not in argv

    def test_no_mount_options_beyond_readonly(self, endpoint):
        """`stat-virt=off` in particular passes real host ownership through and
        denies every write, and its name reads like a simplification."""
        argv = endpoint.create_argv("/msb", ("10.0.0.1",))
        assert not any("stat-virt" in arg for arg in argv)
        assert not any("host-perms" in arg for arg in argv)

    def test_one_flag_per_host_resolver(self, endpoint):
        argv = endpoint.create_argv("/msb", ("10.1.11.166", "10.1.11.168"))
        nameservers = [
            argv[i + 1] for i, arg in enumerate(argv) if arg == "--dns-nameserver"
        ]
        assert nameservers == ["10.1.11.166", "10.1.11.168"]


class TestMounts:
    def volumes(self, endpoint):
        argv = endpoint.create_argv("/msb", ())
        return [argv[i + 1] for i, arg in enumerate(argv) if arg == "--volume"]

    def test_every_mount_lands_at_its_host_path(self, endpoint):
        """Absolute host paths are handed to Globus verbatim, so a source that
        differs from its destination resolves to nothing inside the guest."""
        for volume in self.volumes(endpoint):
            source, destination = volume.split(":")[:2]
            assert source == destination

    def test_the_repository_root_is_not_mounted(self, endpoint, repo_root):
        """It is what holds .env and both deployment refresh tokens."""
        sources = [volume.split(":")[0] for volume in self.volumes(endpoint)]
        assert str(repo_root) not in sources
        assert not any(source == str(repo_root.parent) for source in sources)

    def test_the_data_directory_is_writable(self, endpoint):
        assert f"{endpoint.data_dir}:{endpoint.data_dir}" in self.volumes(endpoint)

    def test_the_jobs_directory_is_mounted_at_its_host_path(self, endpoint):
        jobs = endpoint.hpc_jobs_dir
        assert f"{jobs}:{jobs}" in self.volumes(endpoint)

    def test_no_mount_carries_an_options_suffix(self, endpoint):
        """microsandbox 0.5.7 mis-parses `source:destination:options`: it
        appends the option's last character to the destination and mounts
        read-write anyway, so `:ro` on the jobs directory would put it at
        `…/hpc_jobso` and every upload would find nothing. Confinement is
        `-restrict-paths`, asserted below."""
        for volume in self.volumes(endpoint):
            assert volume.count(":") == 1, volume

    def test_a_jobs_directory_inside_the_data_directory_is_not_mounted_twice(
        self, repo_root
    ):
        nested = Endpoint(repo_root / "data", repo_root / "data" / "hpc_jobs")
        assert nested.mounts() == [nested.data_dir]

    def test_restrict_paths_confines_the_endpoint_inside_the_guest(self, endpoint):
        assert endpoint.restrict_paths == (
            f"r{endpoint.hpc_jobs_dir}/,rw{endpoint.data_dir / 'volumes'}/"
        )


class TestStatus:
    def test_before_setup(self, endpoint, fake_msb):
        status = endpoint.status()
        assert status.state is State.NOT_SET_UP
        assert not status.running

    def test_no_runtime(self, endpoint, monkeypatch, fake_msb):
        set_up(endpoint)
        monkeypatch.setenv("VISTA_MSB_PATH", "/nonexistent/msb")
        status = endpoint.status()
        assert status.state is State.NO_RUNTIME
        assert "microsandbox" in status.detail

    def test_no_image(self, endpoint, monkeypatch, fake_msb):
        set_up(endpoint)
        monkeypatch.setenv("FAKE_IMAGE", "missing")
        status = endpoint.status()
        assert status.state is State.NO_IMAGE
        assert gcp_vm.IMAGE in status.detail

    def test_no_vm(self, endpoint, monkeypatch, fake_msb):
        set_up(endpoint)
        monkeypatch.setenv("FAKE_VM", "none")
        assert endpoint.status().state is State.NOT_STARTED

    def test_running_vm(self, endpoint, monkeypatch, fake_msb):
        set_up(endpoint)
        monkeypatch.setenv("FAKE_VM", "Running")
        status = endpoint.status()
        assert status.state is State.RUNNING
        assert status.running

    def test_a_vm_that_died(self, endpoint, monkeypatch, fake_msb):
        set_up(endpoint)
        monkeypatch.setenv("FAKE_VM", "Stopped")
        status = endpoint.status()
        assert status.state is State.STOPPED
        assert not status.running
        assert "restart" in status.detail

    def test_the_four_vm_states_are_distinct(self, endpoint, monkeypatch, fake_msb):
        set_up(endpoint)
        seen = []
        for image, vm in (
            ("missing", "none"),
            ("present", "none"),
            ("present", "Running"),
            ("present", "Stopped"),
        ):
            monkeypatch.setenv("FAKE_IMAGE", image)
            monkeypatch.setenv("FAKE_VM", vm)
            seen.append(endpoint.status())
        assert len({status.state for status in seen}) == 4
        assert len({status.detail for status in seen}) == 4


class TestActionableFailures:
    def test_starting_before_setup_says_so(self, endpoint, fake_msb):
        with pytest.raises(gcp_vm.EndpointError, match="not set up"):
            endpoint.start()

    def test_a_missing_image_names_the_image(self, endpoint, monkeypatch, fake_msb):
        set_up(endpoint)
        monkeypatch.setenv("FAKE_IMAGE", "missing")
        with pytest.raises(gcp_vm.EndpointError, match=gcp_vm.IMAGE):
            endpoint.create_vm()

    def test_a_missing_runtime_names_the_override(
        self, endpoint, monkeypatch, fake_msb
    ):
        set_up(endpoint)
        monkeypatch.setenv("VISTA_MSB_PATH", "/nonexistent/msb")
        with pytest.raises(gcp_vm.EndpointError, match="VISTA_MSB_PATH"):
            endpoint.create_vm()


class TestGuestCommands:
    def test_non_interactive_commands_get_a_closed_stdin(
        self, endpoint, fake_msb, monkeypatch
    ):
        """`msb exec` blocks indefinitely on an open stdin, which looks exactly
        like a hung endpoint."""
        captured = {}

        def record(argv, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(argv, 0)

        monkeypatch.setattr(gcp_vm.subprocess, "run", record)
        endpoint.run_in_guest(["true"])
        assert captured["stdin"] is subprocess.DEVNULL

    def test_interactive_commands_keep_the_terminal(
        self, endpoint, fake_msb, monkeypatch
    ):
        captured = {}

        def record(argv, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(argv, 0)

        monkeypatch.setattr(gcp_vm.subprocess, "run", record)
        endpoint.run_in_guest(["true"], tty=True)
        assert captured["stdin"] is None

    def test_the_guest_writes_its_state_where_it_survives(self, endpoint):
        """Globus Connect Personal writes into HOME, and the guest's own
        filesystem is discarded with the microVM."""
        argv = endpoint.exec_argv("/msb", ["true"], tty=False)
        assert f"HOME={endpoint.data_dir / 'gcphome'}" in argv

    def test_a_tty_is_asked_for_only_when_wanted(self, endpoint):
        assert "--tty" in endpoint.exec_argv("/msb", ["true"], tty=True)
        assert "--tty" not in endpoint.exec_argv("/msb", ["true"], tty=False)

    def test_the_endpoint_command_carries_its_confinement(self, endpoint):
        command = endpoint.start_command("/gcp")
        assert command[command.index("-restrict-paths") + 1] == endpoint.restrict_paths

    def test_setup_without_a_key_is_the_interactive_form(self, endpoint):
        assert endpoint.setup_command("/gcp", None)[-1] == "--no-gui"

    def test_setup_with_a_key_passes_it(self, endpoint):
        assert endpoint.setup_command("/gcp", "abc123")[-1] == "abc123"


class TestStop:
    def test_stop_removes_the_microvm(self, endpoint, fake_msb):
        endpoint.stop()
        called = fake_msb.read_text()
        assert f"stop {gcp_vm.SANDBOX_NAME}" in called
        assert f"remove {gcp_vm.SANDBOX_NAME}" in called

    def test_stop_is_idempotent(self, endpoint, fake_msb):
        endpoint.stop()
        endpoint.stop()
        assert endpoint._process is None


class TestMsbDiscovery:
    def test_an_override_that_is_not_executable_is_not_used(
        self, tmp_path, monkeypatch
    ):
        not_executable = tmp_path / "msb"
        not_executable.write_text("")
        monkeypatch.setenv("VISTA_MSB_PATH", str(not_executable))
        assert gcp_vm.msb_path() is None

    def test_the_bundled_binary_is_found_without_an_override(self, monkeypatch):
        """Found in dev_mcp_server's environment, which this package does not
        depend on and cannot import."""
        monkeypatch.delenv("VISTA_MSB_PATH", raising=False)
        found = gcp_vm.msb_path()
        assert found is not None
        assert found.name == "msb"
        assert os.access(found, os.X_OK)


class TestSetup:
    def test_a_pipe_is_refused_with_instructions(self, endpoint, fake_msb):
        """Globus Connect Personal prints an address and waits for what the
        browser login returns. A pipe cannot answer, and hanging on a prompt
        nobody can see is worse than refusing."""
        with pytest.raises(gcp_vm.EndpointError) as refusal:
            endpoint.setup(None, interactive=False)
        assert "terminal" in str(refusal.value)
        assert "GLOBUS_SETUP_KEY" in str(refusal.value)

    def test_a_setup_key_needs_no_terminal(
        self, endpoint, fake_msb, installed_gcp, monkeypatch
    ):
        seen = {}

        def record(argv, **kwargs):
            seen["argv"] = argv
            endpoint.client_id_file.parent.mkdir(parents=True, exist_ok=True)
            endpoint.client_id_file.write_text("made-by-setup\n")
            return subprocess.CompletedProcess(argv, 0)

        monkeypatch.setattr(gcp_vm.subprocess, "run", record)
        endpoint.setup("a-setup-key", interactive=False)
        assert seen["argv"][-1] == "a-setup-key"

    def test_an_interactive_terminal_asks_for_one_in_the_guest(
        self, endpoint, fake_msb, installed_gcp, monkeypatch
    ):
        """`msb exec --tty` is the direct analogue of the `docker run -t` this
        replaces; Globus Connect Personal prompts only when it has a terminal."""
        seen = {}

        def record(argv, read):
            seen["argv"] = argv
            endpoint.client_id_file.parent.mkdir(parents=True, exist_ok=True)
            endpoint.client_id_file.write_text("made-by-setup\n")
            return 0

        monkeypatch.setattr(gcp_vm.pty, "spawn", record)
        endpoint.setup(None, interactive=True)
        assert "--tty" in seen["argv"]

    def test_setup_that_writes_no_collection_is_a_failure(
        self, endpoint, fake_msb, installed_gcp, monkeypatch
    ):
        monkeypatch.setattr(
            gcp_vm.subprocess,
            "run",
            lambda argv, **kw: subprocess.CompletedProcess(argv, 0),
        )
        with pytest.raises(gcp_vm.EndpointError, match="without writing"):
            endpoint.setup("a-setup-key", interactive=False)

    def test_setup_is_skipped_once_the_collection_exists(
        self, endpoint, fake_msb, monkeypatch
    ):
        set_up(endpoint)

        def explode(*args, **kwargs):
            raise AssertionError("setup ran again")

        monkeypatch.setattr(gcp_vm.subprocess, "run", explode)
        monkeypatch.setattr(gcp_vm.pty, "spawn", explode)
        endpoint.setup(None, interactive=True)


class TestLoginUrl:
    @pytest.mark.parametrize(
        "output, expected",
        [
            (
                b"Please log in at:\n  https://auth.globus.org/v2/oauth2/authorize?c=1\n",
                "https://auth.globus.org/v2/oauth2/authorize?c=1",
            ),
            (
                b"see https://app.globus.org/file-manager.",
                "https://app.globus.org/file-manager",
            ),
            (b"no address yet, still starting", None),
            (b"https://example.com/unrelated", None),
        ],
    )
    def test_the_address_is_recognised_however_it_is_worded(self, output, expected):
        assert gcp_vm._first_login_url(output) == expected

    def test_the_address_is_printed_when_no_opener_exists(self, monkeypatch, capsys):
        """A WSL installation commonly has no browser and no opener, and setup
        still has to be completable there by copy-and-paste."""

        def no_opener(*args, **kwargs):
            raise FileNotFoundError("xdg-open")

        monkeypatch.setattr(gcp_vm.subprocess, "run", no_opener)
        gcp_vm.open_login_url("https://auth.globus.org/login")
        assert "https://auth.globus.org/login" in capsys.readouterr().out

    def test_every_opener_is_tried_before_giving_up(self, monkeypatch, capsys):
        monkeypatch.setattr(gcp_vm.sys, "platform", "linux")
        tried = []

        def failing(argv, **kwargs):
            tried.append(argv[0])
            return subprocess.CompletedProcess(argv, 1)

        monkeypatch.setattr(gcp_vm.subprocess, "run", failing)
        gcp_vm.open_login_url("https://auth.globus.org/login")
        assert tried == ["xdg-open", "wslview"]

    def test_the_address_is_printed_when_a_browser_did_open(self, monkeypatch, capsys):
        monkeypatch.setattr(gcp_vm.sys, "platform", "darwin")
        monkeypatch.setattr(
            gcp_vm.subprocess,
            "run",
            lambda argv, **kw: subprocess.CompletedProcess(argv, 0),
        )
        gcp_vm.open_login_url("https://auth.globus.org/login")
        assert "https://auth.globus.org/login" in capsys.readouterr().out


class TestResolveGcp:
    def test_an_existing_install_is_not_downloaded_again(self, endpoint, monkeypatch):
        """It is installed under the data directory so it persists when data/ is
        a mounted volume."""
        installed = (
            endpoint.data_dir / "globusconnectpersonal" / "globusconnectpersonal"
        )
        installed.parent.mkdir(parents=True)
        installed.write_text("#!/bin/sh\n")

        def explode(*args, **kwargs):
            raise AssertionError("downloaded an install that was already there")

        monkeypatch.setattr(gcp_vm.urllib.request, "urlopen", explode)
        assert endpoint.resolve_gcp() == installed

    def test_the_host_binary_is_never_used_under_the_microvm(
        self, endpoint, monkeypatch, fake_msb
    ):
        """On macOS `which globusconnectpersonal` finds a Darwin executable,
        which cannot run in a Linux guest."""
        monkeypatch.setattr(gcp_vm, "uses_microvm", lambda: True)
        called = []
        monkeypatch.setattr(
            "shutil.which", lambda name: called.append(name) or "/usr/local/bin/gcp"
        )
        installed = (
            endpoint.data_dir / "globusconnectpersonal" / "globusconnectpersonal"
        )
        installed.parent.mkdir(parents=True)
        installed.write_text("#!/bin/sh\n")

        assert endpoint.resolve_gcp() == installed
        assert "globusconnectpersonal" not in called

    def test_the_host_binary_is_used_on_the_native_path(self, endpoint, monkeypatch):
        monkeypatch.setattr(gcp_vm, "uses_microvm", lambda: False)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/gcp")
        assert endpoint.resolve_gcp() == Path("/usr/local/bin/gcp")

    def test_an_unsupported_architecture_is_named(
        self, endpoint, monkeypatch, fake_msb
    ):
        monkeypatch.setattr(gcp_vm.platform, "machine", lambda: "riscv64")
        with pytest.raises(gcp_vm.EndpointError, match="riscv64"):
            endpoint.resolve_gcp()


class TestCommandLine:
    """The entry point both callers reach.

    `scripts/launch_globus.py` delegates here, and the packaged launcher runs it
    as `python -m vista_mcp_server.lib.gcp_vm`, because `scripts/` is not
    installed. Anything asserted here therefore holds for both.
    """

    def test_the_environment_names_the_directories(self, repo_root, monkeypatch):
        """The same two variables `vista_mcp_server` itself reads, which is what
        makes the mounts the directories the transfers name."""
        monkeypatch.setenv("VISTA_DATA_DIR", str(repo_root / "data"))
        monkeypatch.setenv("VISTA_MCP_LOCAL_HPC_JOBS_DIR", str(repo_root / "hpc_jobs"))

        endpoint = gcp_vm.endpoint_from_environment()

        assert endpoint.data_dir == repo_root / "data"
        assert endpoint.hpc_jobs_dir == repo_root / "hpc_jobs"

    def test_status_exits_non_zero_when_not_running(
        self, endpoint, fake_msb, monkeypatch, capsys
    ):
        """The launcher gates its startup warning on this exit status rather
        than parsing the line, so the two have to agree."""
        monkeypatch.setattr(gcp_vm, "endpoint_from_environment", lambda: endpoint)

        assert gcp_vm.main(["--status"]) == 1
        assert capsys.readouterr().out.strip() == endpoint.status().detail

    def test_status_exits_zero_only_when_running(self, endpoint, fake_msb, monkeypatch):
        set_up(endpoint)
        monkeypatch.setenv("FAKE_VM", "running")
        monkeypatch.setattr(gcp_vm, "endpoint_from_environment", lambda: endpoint)

        assert gcp_vm.main(["--status"]) == 0

    def test_start_does_not_attempt_setup(self, endpoint, fake_msb, monkeypatch):
        """The launcher runs setup on a terminal and starts the endpoint in the
        background, where stdin is not one. Were `--start` to attempt setup, an
        installation that was already set up would be fine and a first run would
        refuse for want of a terminal it was never supposed to need."""
        monkeypatch.setattr(gcp_vm, "endpoint_from_environment", lambda: endpoint)

        def refuse(*args, **kwargs):
            raise AssertionError("--start attempted setup")

        monkeypatch.setattr(Endpoint, "setup", refuse)
        # Not set up, so `start` is what reports -- the point being that the
        # refusal comes from starting, not from an unasked-for setup.
        assert gcp_vm.main(["--start"]) == 1

    def test_setup_reports_its_failure_as_a_line_not_a_traceback(
        self, endpoint, fake_msb, monkeypatch, capsys
    ):
        monkeypatch.setattr(gcp_vm, "endpoint_from_environment", lambda: endpoint)
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        assert gcp_vm.main(["--setup"]) == 1
        assert capsys.readouterr().err.startswith("error: ")

    def test_the_modes_are_exclusive(self):
        """Each answers a different question, and a caller that passed two would
        silently get whichever the implementation happened to check first."""
        with pytest.raises(SystemExit):
            gcp_vm.main(["--setup", "--status"])
