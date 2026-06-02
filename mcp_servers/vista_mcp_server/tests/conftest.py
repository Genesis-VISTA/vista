"""Shared fixtures for vista_mcp_server tests.

The MCP server's settings are validated at import time, so we set the
minimum environment for tests to pass.
"""
import os

# Required by vista_mcp_server.config.AppSettings.
os.environ.setdefault("VISTA_MCP_HPC_SSH_USER", "test-user")
# Required by agenthpc.config.get_app_config when reading the monbtaw app.
os.environ.setdefault("VISTA_AGENTHPC_ALLOY_DIR", "/tmp/test-monbtaw-workdir")


import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
