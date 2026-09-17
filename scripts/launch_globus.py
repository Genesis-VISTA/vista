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
from vista_mcp_server.lib.gcp_vm import main as endpoint_main


def main() -> None:
    """Supply a development checkout's context, then hand over.

    The two things this adds are the two the packaged launcher supplies by other
    means: a working directory the relative data paths resolve against, and the
    repository `.env` the refresh tokens live in. Every option, and every
    decision about what they mean, belongs to `gcp_vm` -- the packaged launcher
    runs that same entry point as `python -m`, and a second argument parser here
    is a second place for the two to disagree.
    """
    os.chdir(REPO_ROOT)
    load_dotenv(ENV_FILE)
    sys.exit(endpoint_main())


if __name__ == "__main__":
    main()
