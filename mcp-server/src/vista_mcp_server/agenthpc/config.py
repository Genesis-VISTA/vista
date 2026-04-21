"""
Loader for the agenthpc per-application config (applications.yaml).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


_CONFIG_PATH = Path(__file__).parent / "applications.yaml"


@lru_cache(maxsize=1)
def _load() -> dict[str, dict[str, Any]]:
    with open(_CONFIG_PATH) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{_CONFIG_PATH} must be a YAML mapping of app_type -> config")
    return data


def available_apps() -> list[str]:
    return list(_load().keys())


def get_app_config(app_type: str) -> dict[str, Any]:
    apps = _load()
    if app_type not in apps:
        raise ValueError(
            f"Unknown agenthpc application '{app_type}'. "
            f"Available: {', '.join(sorted(apps.keys())) or '(none)'}"
        )
    return apps[app_type]
