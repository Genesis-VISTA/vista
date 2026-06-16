#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14, <3.15"
# dependencies = ["python-dotenv"]
# ///
"""Launch the Vista-side Globus Connect Personal endpoint, exposing:
  - hpc_jobs/        (source uploads:  Vista -> OLCF)
  - data/volumes/    (per-project x user output dirs; downloads + log fetches)

Pass --save-env to write the collection UUID to .env as
VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID (otherwise it is just printed).

First-time setup needs a one-time Globus login. Either:
  - run this script once in an interactive terminal (browser login flow), or
  - set GLOBUS_SETUP_KEY for headless setup, create the key with:
        uvx --from globus-cli globus gcp create mapped "vista-server"

Globus Connect Personal only ships a Linux CLI, so on macOS (and any other
non-Linux host) this script transparently re-launches itself inside a Linux
docker/podman container, mounting the repo and data dirs at their host paths.
"""
from __future__ import annotations

import argparse
import os
import platform
import textwrap
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

from dotenv import load_dotenv, set_key

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"

GCP_TARBALL_URLS = {
    "x86_64": "https://downloads.globus.org/globus-connect-personal/linux/stable/globusconnectpersonal-latest.tgz",
    "aarch64": "https://downloads.globus.org/globus-connect-personal/linux_aarch64/stable/globusconnectpersonal-aarch64-latest.tgz",
}


def die(*lines: str) -> None:
    print(*lines, sep="\n", file=sys.stderr)
    sys.exit(1)


def resolve_gcp(data_dir: Path) -> str:
    """Locate the globusconnectpersonal executable, installing it on Linux."""
    gcp = shutil.which("globusconnectpersonal")
    if gcp:
        return gcp

    arch = platform.machine()
    arch = {"arm64": "aarch64", "amd64": "x86_64"}.get(arch, arch)
    url = GCP_TARBALL_URLS.get(arch)
    if url is None:
        die(f"error: no Globus Connect Personal build for architecture {arch!r}")

    # Install under the data dir so it persists when data/ is a k8s volume.
    install_dir = data_dir / "globusconnectpersonal"
    gcp = str(install_dir / "globusconnectpersonal")
    if not Path(gcp).exists():
        print(f"globusconnectpersonal not found; installing to {install_dir} ...")
        install_dir.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url) as resp:
            with tarfile.open(fileobj=resp, mode="r|gz") as tar:
                def _strip_top_level(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo | None:
                    """Drop the leading 'globusconnectpersonal-x.y.z/' path component."""
                    _, _, member.name = member.name.partition("/")
                    return member if member.name else None
                tar.extractall(install_dir, filter=_strip_top_level)
    return gcp


def relaunch_in_container(data_dir: Path, hpc_jobs_dir: Path, argv: list[str]) -> None:
    """Re-exec this script inside a Linux container. """
    runtime = shutil.which("docker") or shutil.which("podman")
    if not runtime:
        die(
            "error: neither docker nor podman found on PATH",
            f"(required to run Globus Connect Personal on {platform.system()})",
        )
        return

    # Build the image once so deps aren't reinstalled on every run.
    print("Building vista-globus image ...", file=sys.stderr)
    dockerfile = textwrap.dedent(r"""
        FROM ubuntu:24.04
        RUN apt-get update -qq \
            && apt-get install -y -qq curl ca-certificates python3 python3-dotenv \
            && rm -rf /var/lib/apt/lists/*
    """)
    subprocess.run(
        [runtime, "build", "-t", "vista-globus", "-"],
        input=dockerfile.encode(),
        check=True,
    )
    subprocess.run(
        [runtime, "rm", "-f", "vista-globus"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    cmd = [runtime, "run", "--rm", "-i"]
    if sys.stdin.isatty():
        cmd.append("-t")
    cmd += ["--name", "vista-globus", "-e", "GLOBUS_SETUP_KEY", "-v", "vista-gcp-home:/root"]
    # Mount the repo plus any data/hpc dirs that live outside it, at matching paths.
    mounts = [REPO_ROOT]
    mounts += [d for d in (data_dir, hpc_jobs_dir) if not d.is_relative_to(REPO_ROOT)]
    for d in mounts:
        cmd += ["-v", f"{d}:{d}"]
    cmd += ["-w", str(REPO_ROOT), "vista-globus", "python3", "scripts/launch_globus.py", *argv]
    os.execvp(runtime, cmd)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch the Vista-side Globus Connect Personal endpoint.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--save-env",
        action="store_true",
        help="Write the collection UUID to .env as VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID (else just print it).",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run first-time setup and exit without starting the endpoint.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.chdir(REPO_ROOT)
    load_dotenv(ENV_FILE)

    data_dir = Path(os.environ.get("VISTA_DATA_DIR", "./data")).resolve()
    hpc_jobs_dir = Path(os.environ.get("VISTA_MCP_LOCAL_HPC_JOBS_DIR", "./hpc_jobs")).resolve()
    volumes_dir = data_dir / "volumes"
    config_dir = data_dir / "globusonline"
    for d in (hpc_jobs_dir, volumes_dir, config_dir):
        d.mkdir(parents=True, exist_ok=True)

    if sys.platform != "linux":
        relaunch_in_container(data_dir, hpc_jobs_dir, sys.argv[1:])

    setup_key = os.environ.get("GLOBUS_SETUP_KEY")
    client_id_file = config_dir / "lta" / "client-id.txt"
    if not client_id_file.exists() and not setup_key and not sys.stdin.isatty():
        die(
            "error: Globus Connect Personal is not set up. Run 'launch_globus.py --setup' in an",
            "interactive terminal or set GLOBUS_SETUP_KEY",
        )

    gcp = resolve_gcp(data_dir)
    if not client_id_file.exists():
        if setup_key:
            subprocess.run([gcp, "-dir", str(config_dir), "-setup", setup_key], check=True)
        else:
            subprocess.run([gcp, "-dir", str(config_dir), "-setup", "--no-gui"], check=True)

    collection_id = client_id_file.read_text().strip()
    if args.save_env:
        ENV_FILE.touch(exist_ok=True)
        set_key(ENV_FILE, "VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID", collection_id, quote_mode="never")
        print(f"Wrote VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID={collection_id} to .env")
    else:
        print(f"VISTA_MCP_VISTA_GLOBUS_COLLECTION_ID={collection_id}")
        print("(pass --save-env to write this to .env)")

    print("Globus endpoint setup complete.")
    if not args.setup:
        restrict_paths = f"r{hpc_jobs_dir}/,rw{volumes_dir}/"
        os.execv(gcp, [gcp, "-dir", str(config_dir), "-start", "-restrict-paths", restrict_paths])

if __name__ == "__main__":
    main()
