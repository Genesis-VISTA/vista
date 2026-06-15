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
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

from dotenv import load_dotenv, set_key

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"

GCP_TARBALL_URL = "https://downloads.globus.org/globus-connect-personal/linux/stable/globusconnectpersonal-latest.tgz"


def die(*lines: str) -> None:
    print(*lines, sep="\n", file=sys.stderr)
    sys.exit(1)


def resolve_gcp(data_dir: Path) -> str:
    """Locate the globusconnectpersonal executable, installing it on Linux."""
    gcp = shutil.which("globusconnectpersonal")
    if gcp:
        return gcp

    if sys.platform == "darwin":
        # The macOS GUI app can't be auto-downloaded; require a manual install.
        gcp = "/Applications/Globus Connect Personal.app/Contents/MacOS/globusconnectpersonal"
        if not Path(gcp).exists():
            die(
                "error: globusconnectpersonal not found",
                "Install Globus Connect Personal from",
                "  https://www.globus.org/globus-connect-personal",
            )
    elif sys.platform == "linux":
        # Linux: install under the data dir so it persists when data/ is a k8s volume.
        install_dir = data_dir / "globusconnectpersonal"
        gcp = str(install_dir / "globusconnectpersonal")
        if not Path(gcp).exists():
            print(f"globusconnectpersonal not found; installing to {install_dir} ...")
            install_dir.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(GCP_TARBALL_URL) as resp:
                with tarfile.open(fileobj=resp, mode="r|gz") as tar:
                    def _strip_top_level(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo | None:
                        """Drop the leading 'globusconnectpersonal-x.y.z/' path component."""
                        _, _, member.name = member.name.partition("/")
                        return member if member.name else None
                    tar.extractall(install_dir, filter=_strip_top_level)
    else:
        die("Platform not supported")
    return gcp


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
