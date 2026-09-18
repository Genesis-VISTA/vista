"""
`display_file` resolves a path inside the sandbox to something the browser can
fetch. It has no fallback: a URI that matches nothing in the map is refused, so
a plot the agent has just written cannot be shown.

The map itself is built by the backend, per call, in `_build_vista_metadata`.
The two packages have separate virtualenvs and cannot import each other, so the
templates below are duplicated from that function deliberately — the backend
pins the exact map it sends, and this pins that such a map resolves.
"""

import pytest

from vista_mcp_server.display_file_mcp import resolve_uri

pytestmark = pytest.mark.unit

# Verbatim from vista_backend.agents.agents.ProjectAgent._build_vista_metadata.
URI_MAP = {
    "file:///mnt/data/output/{path}": "/api/files/outputs/{path}?project_name=molten-salt",
    "file:///mnt/data/uploads/{path}": "/api/files/uploads/{path}?project_name=molten-salt",
}


def test_resolves_the_absolute_path_the_agent_reported():
    """The exact shape that failed: a bare absolute path to a nested plot."""
    assert (
        resolve_uri("/mnt/data/output/salt-plots/mstdb_overview.png", URI_MAP)
        == "/api/files/outputs/salt-plots/mstdb_overview.png?project_name=molten-salt"
    )


def test_resolves_the_same_path_given_as_a_file_uri():
    """The tool's argument is documented as either, so both have to work."""
    assert (
        resolve_uri("file:///mnt/data/output/plot.png", URI_MAP)
        == "/api/files/outputs/plot.png?project_name=molten-salt"
    )


def test_uploads_resolve_too():
    assert (
        resolve_uri("/mnt/data/uploads/input.csv", URI_MAP)
        == "/api/files/uploads/input.csv?project_name=molten-salt"
    )


def test_a_path_outside_the_mapped_roots_is_refused():
    """The refusal is the security boundary, so it has to stay a refusal."""
    with pytest.raises(ValueError, match="No download URL is configured"):
        resolve_uri("/etc/passwd", URI_MAP)


def test_an_empty_map_refuses_everything():
    """
    This was the bug: the backend never sent a map, so every path fell through
    to the refusal and no plot could ever be displayed.
    """
    with pytest.raises(ValueError, match="No download URL is configured"):
        resolve_uri("/mnt/data/output/plot.png", {})


def test_traversal_out_of_the_sandbox_is_refused():
    with pytest.raises(ValueError):
        resolve_uri("file:///mnt/data/output/../../etc/passwd", URI_MAP)


def test_spaces_in_a_filename_survive_as_percent_escapes():
    """The result goes into an <img src>, so it has to be a usable URL."""
    assert (
        resolve_uri("/mnt/data/output/phase diagram.png", URI_MAP)
        == "/api/files/outputs/phase%20diagram.png?project_name=molten-salt"
    )
