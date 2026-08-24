"""
Tests for display_file's sandbox-path -> download-URL resolution.

`resolve_uri` is the whole tool: it maps a path the agent holds (normally a
sandbox path — `get_hpc_job_outputs` and the dev sandbox both return
`/mnt/data/...`) onto a URL the browser can fetch. It has no fallback: a URI that
matches nothing in the map is refused, so a plot the agent has just written
cannot be shown.

The map itself is supplied per call by the backend in the `vista` MCP metadata;
see `_build_vista_metadata` in backend/src/vista_backend/agents. The two packages
have separate virtualenvs and cannot import each other, so the templates below
are duplicated from that function deliberately — the backend pins the exact map
it sends, and this pins that such a map resolves.

Regression context: the backend used to omit `uri_map` entirely, so it defaulted
to `{}` and every display_file call failed with "No download URL is configured" —
the files existed on disk and simply could not be served.
"""

import pytest

from vista_mcp_server.display_file_mcp import resolve_uri

pytestmark = pytest.mark.unit

PROJECT = "water4energy"
HOST_OUT = "/srv/vista/volumes/proj-user/data/output"

# The shape the backend ships.
URI_MAP = {
    "file:///mnt/data/output/{path}": f"/api/files/outputs/{{path}}?project_name={PROJECT}",
    f"file://{HOST_OUT}/{{path}}": f"/api/files/outputs/{{path}}?project_name={PROJECT}",
    "file:///mnt/data/uploads/{path}": f"/api/files/uploads/{{path}}?project_name={PROJECT}",
}

JOB = "4408123"


@pytest.mark.parametrize(
    "uri",
    [
        f"/mnt/data/output/{JOB}/surface_temperature_comparison.png",
        f"file:///mnt/data/output/{JOB}/surface_temperature_comparison.png",
    ],
)
def test_hpc_job_output_path_resolves(uri):
    """A bare path and a file:// URI for the same file both resolve identically."""
    assert resolve_uri(uri, URI_MAP) == (
        f"/api/files/outputs/{JOB}/surface_temperature_comparison.png"
        f"?project_name={PROJECT}"
    )


def test_host_volume_path_also_resolves():
    assert resolve_uri(f"{HOST_OUT}/{JOB}/results.json", URI_MAP) == (
        f"/api/files/outputs/{JOB}/results.json?project_name={PROJECT}"
    )


def test_uploads_prefix_resolves_to_the_uploads_route():
    assert resolve_uri("/mnt/data/uploads/notes.txt", URI_MAP) == (
        f"/api/files/uploads/notes.txt?project_name={PROJECT}"
    )


def test_filename_needing_encoding_is_percent_encoded_but_keeps_separators():
    resolved = resolve_uri("/mnt/data/output/plots/my figure (v2).png", URI_MAP)
    assert resolved == (
        f"/api/files/outputs/plots/my%20figure%20%28v2%29.png?project_name={PROJECT}"
    )


def test_empty_map_is_the_regression_being_guarded():
    """The pre-fix behavior: no map -> nothing can ever be displayed."""
    with pytest.raises(ValueError, match="No download URL is configured"):
        resolve_uri(f"/mnt/data/output/{JOB}/plot.png", {})


def test_parent_traversal_is_rejected():
    """`..` survives pathlib normalization, so the component check catches it."""
    with pytest.raises(ValueError, match="not absolute"):
        resolve_uri("/mnt/data/output/../../etc/passwd", URI_MAP)


def test_single_dot_component_normalizes_to_the_same_file():
    """A `.` is a no-op that pathlib drops; the path stays inside the mapped prefix."""
    assert resolve_uri("/mnt/data/output/./plot.png", URI_MAP) == (
        f"/api/files/outputs/plot.png?project_name={PROJECT}"
    )


@pytest.mark.parametrize("uri", ["/etc/passwd", "relative/path.png"])
def test_unmapped_paths_are_refused(uri):
    """Only the mapped prefixes are servable; anything else has no URL."""
    with pytest.raises(ValueError, match="No download URL is configured"):
        resolve_uri(uri, URI_MAP)
