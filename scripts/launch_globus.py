#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14, <3.15"
# dependencies = ["python-dotenv"]
# ///
"""Launch the Vista-side Globus Connect Personal endpoint, exposing:
  - hpc_jobs/        (source uploads:  Vista -> OLCF)
  - data/volumes/    (per-project x user output dirs; downloads + log fetches)

First-time setup needs a one-time Globus login. Either:
  - run this script once in an interactive terminal (browser login flow), or
  - set GLOBUS_SETUP_KEY for headless setup, create the key with:
        uvx --from globus-cli globus gcp create mapped "vista-server"

Globus Connect Personal only ships a Linux command-line build, so on macOS (and
any other non-Linux host) the endpoint runs inside a microsandbox microVM. That
needs no container runtime and no daemon.

This script is a wrapper for development checkouts. Everything it does lives in
`vista_mcp_server.lib.gcp_vm`, because `scripts/` is not installed by the
packaged artifact and the two must not drift.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"

# `gcp_vm` imports nothing outside the standard library, so it can be used from
# this script's own environment without installing vista_mcp_server.
sys.path.insert(0, str(REPO_ROOT / "mcp_servers" / "vista_mcp_server" / "src"))

# Imported after the path is set, deliberately.
from vista_mcp_server.lib.gcp_vm import (
    Endpoint,
    EndpointError,
    install_termination_handler,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch the Vista-side Globus Connect Personal endpoint.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run first-time setup and exit without starting the endpoint.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Report whether the endpoint is running, and exit.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.chdir(REPO_ROOT)
    load_dotenv(ENV_FILE)

    endpoint = Endpoint(
        data_dir=Path(os.environ.get("VISTA_DATA_DIR", "./data")).resolve(),
        hpc_jobs_dir=Path(
            os.environ.get("VISTA_MCP_LOCAL_HPC_JOBS_DIR", "./hpc_jobs")
        ).resolve(),
    )

    if args.status:
        print(endpoint.status().detail)
        return

    try:
        endpoint.setup(
            os.environ.get("GLOBUS_SETUP_KEY"), interactive=sys.stdin.isatty()
        )
        print("Globus endpoint setup complete.")
        if args.setup:
            return
        install_termination_handler()
        sys.exit(endpoint.start().wait())
    except EndpointError as error:
        sys.exit(f"error: {error}")


if __name__ == "__main__":
    main()
