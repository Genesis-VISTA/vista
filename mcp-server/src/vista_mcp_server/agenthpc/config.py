"""
Loader for the agenthpc per-application config (applications.yaml).

Fields that reference the simulation code on the HPC side (notably ``work_dir``)
are intentionally left empty in the YAML — a ``work_dir_env`` key names the
environment variable the operator must set to point at the real directory.
``get_app_config`` resolves that env var on read, so tools fail fast with a
clear message when it is missing.
"""

from __future__ import annotations

import os
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
    # Work on a shallow copy so env-var overrides do not mutate the cached YAML.
    cfg = dict(apps[app_type])

    env_key = cfg.get("work_dir_env")
    if env_key:
        from_env = os.environ.get(env_key)
        if from_env:
            cfg["work_dir"] = from_env

    if not cfg.get("work_dir"):
        raise ValueError(
            f"agenthpc application '{app_type}' has no work_dir configured. "
            f"Set the {env_key} environment variable to the simulation code "
            f"directory on {cfg.get('host', 'the HPC host')} (e.g. "
            f"/lustre/orion/scratch/<user>/.../MoNbTaW)."
        )
    return cfg
