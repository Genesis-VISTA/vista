"""The backend's copy of the HPC cluster endpoints must match the MCP server's.

`HpcClusterSettings` reads the MCP server's own `VISTA_MCP_*` variables, so an
override moves both services together. The *defaults* are still two copies.
If they drift, the NavRail checks one endpoint while jobs go to another, and
the card is green for a cluster whose jobs fail. The MCP package isn't
installed in the backend's environment, so its `config.py` is parsed rather
than imported.
"""

import ast
from pathlib import Path

from vista_backend.config import HpcClusterSettings
from vista_backend.services import globus_auth

_MCP_CONFIG = (
    Path(__file__).resolve().parents[2]
    / "mcp_servers/vista_mcp_server/src/vista_mcp_server/config.py"
)


def _literal_defaults(source: str) -> dict[str, object]:
    """`name: T = <literal>` fields of every class in `source`."""
    defaults: dict[str, object] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if (
                isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
                and stmt.value is not None
            ):
                try:
                    defaults[stmt.target.id] = ast.literal_eval(stmt.value)
                except ValueError:
                    continue  # a computed default; nothing to compare
    return defaults


def _mismatches(mcp_source: str) -> dict[str, tuple[object, object]]:
    """Backend fields whose default differs from (or is absent in) the MCP's."""
    mcp = _literal_defaults(mcp_source)
    out: dict[str, tuple[object, object]] = {}
    for name, field in HpcClusterSettings.model_fields.items():
        if mcp.get(name, "<missing>") != field.default:
            out[name] = (field.default, mcp.get(name, "<missing>"))
    return out


def test_backend_hpc_defaults_match_the_mcp_server():
    assert _mismatches(_MCP_CONFIG.read_text(encoding="utf-8")) == {}


def test_globus_connect_collections_match_the_mcp_server():
    """The collection a user consents to must be the one the MCP server reads."""
    mcp = _literal_defaults(_MCP_CONFIG.read_text(encoding="utf-8"))
    for cluster in ("odo", "frontier"):
        assert globus_auth._DEFAULTS[cluster] == mcp[f"{cluster}_globus_collection_id"]


def test_parity_check_catches_a_drifted_default():
    """The check has to fail when a default moves on only one side."""
    source = _MCP_CONFIG.read_text(encoding="utf-8")
    drifted = source.replace(
        '"https://amsc-open.s3m.olcf.ornl.gov"', '"https://drifted.example"', 1
    )
    assert drifted != source, "fixture no longer finds the Odo IRI URL"
    assert set(_mismatches(drifted)) == {"odo_iri_url"}
