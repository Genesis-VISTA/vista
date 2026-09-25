"""Each OLCF cluster is authorized by its own S3M token, with no shared fallback.

An S3M token is scoped to one OLCF project, so a single token cannot serve
both Odo and Frontier. The backend sends `odo_s3m_token` / `frontier_s3m_token`
only; a legacy `s3m_token` key, if one ever arrives, must enable nothing.
"""

import pytest
from fastmcp.exceptions import ToolError

from vista_mcp_server.lib.user_config import UserConfig
from vista_mcp_server.submit_job_mcp import _default_cluster


def test_legacy_token_is_ignored():
    cfg = UserConfig.model_validate({"s3m_token": "legacy"})
    for cluster in ("odo", "frontier"):
        with pytest.raises(ToolError, match=f"No S3M token configured for '{cluster}'"):
            cfg.require_s3m_token(cluster)
    with pytest.raises(ToolError):
        _default_cluster(cfg)


def test_each_token_authorizes_only_its_cluster():
    cfg = UserConfig(odo_s3m_token="odo-tok")
    assert cfg.require_s3m_token("odo") == "odo-tok"
    with pytest.raises(ToolError):
        cfg.require_s3m_token("frontier")
    assert _default_cluster(cfg) == "odo"

    cfg = UserConfig(frontier_s3m_token="fr-tok")
    assert cfg.require_s3m_token("frontier") == "fr-tok"
    with pytest.raises(ToolError):
        cfg.require_s3m_token("odo")
    assert _default_cluster(cfg) == "frontier"


def test_both_tokens_enable_both_clusters():
    cfg = UserConfig(odo_s3m_token="odo-tok", frontier_s3m_token="fr-tok")
    assert cfg.require_s3m_token("odo") == "odo-tok"
    assert cfg.require_s3m_token("frontier") == "fr-tok"
    # Two configured clusters means the caller must pick one explicitly.
    with pytest.raises(ToolError, match='"frontier", "odo"'):
        _default_cluster(cfg)
