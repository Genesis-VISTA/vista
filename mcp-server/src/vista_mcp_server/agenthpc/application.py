"""
Application-specific search space and scoring logic for the agenthpc sub-MCP.

Ported from the original AgentHPC/src/application.py but trimmed to the pieces
Vista's agent actually needs: search-space metadata, log-file parsing, and
score calculation. Pure Python — no HPC / SSH imports live here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np


def key_from_params(*params) -> str:
    """Stable string key for deduplicating / caching results by parameter set."""
    if len(params) == 4:
        e1, e2, e3, e4 = params
        return f"{e1:.2f}_{e2:.2f}_{e3:.2f}_{e4:.2f}"
    raise ValueError(f"Unsupported number of parameters: {len(params)}")


class BaseApplication(ABC):
    """Per-application search space + scoring."""

    def __init__(self, app_type: str, app_config: dict[str, Any]):
        self.app_type = app_type
        self.app_config = app_config
        self.search_space = self._setup_search_space()
        self.total_search_space = self._calculate_total_space()

    @abstractmethod
    def _setup_search_space(self) -> dict:
        ...

    @abstractmethod
    def _calculate_total_space(self) -> int:
        ...

    @abstractmethod
    def get_search_space_info(self) -> dict:
        ...

    @abstractmethod
    def parse_log(self, log_contents: str) -> tuple | None:
        """Extract raw metrics from a log file's contents. None if unparseable."""
        ...

    @abstractmethod
    def calculate_score(self, metrics: tuple | None) -> float:
        ...

    @property
    def score_threshold(self) -> float:
        return float(self.app_config["score_threshold"])

    @property
    def max_trials(self) -> int:
        return int(self.app_config["max_trials"])

    @property
    def host(self) -> str:
        return str(self.app_config["host"])


class MoNbTaWApplication(BaseApplication):
    """MoNbTaW 4-element refractory high entropy alloy optimization."""

    def _setup_search_space(self) -> dict:
        sc = self.app_config["search_space"]
        def linspace(key_range: str, key_steps: str) -> list[float]:
            return [round(v, 2) for v in np.linspace(
                sc[key_range][0], sc[key_range][1], sc[key_steps]
            ).tolist()]
        return {
            "Mo_VALUES": linspace("Mo_range", "Mo_steps"),
            "Nb_VALUES": linspace("Nb_range", "Nb_steps"),
            "Ta_VALUES": linspace("Ta_range", "Ta_steps"),
            "W_VALUES": linspace("W_range", "W_steps"),
        }

    def _calculate_total_space(self) -> int:
        s = self.search_space
        return (len(s["Mo_VALUES"]) * len(s["Nb_VALUES"])
                * len(s["Ta_VALUES"]) * len(s["W_VALUES"]))

    def get_search_space_info(self) -> dict:
        return {
            "application": self.app_type,
            "goal": self.app_config.get("goal", ""),
            "element_names": ["Mo", "Nb", "Ta", "W"],
            "value_range": [
                self.search_space["Mo_VALUES"][0],
                self.search_space["Mo_VALUES"][-1],
            ],
            "allowed_values": self.search_space["Mo_VALUES"],
            "constraint": "Mo + Nb + Ta + W must sum to 1.0",
            "num_params": 4,
            "total_combinations": self.total_search_space,
            "score_threshold": self.score_threshold,
            "max_trials": self.max_trials,
        }

    def parse_log(self, log_contents: str) -> tuple[float, float] | None:
        """
        stat0.dat columns: <temperature> <?> <specific_heat>
        Return (temperature_at_max_cv, max_cv).
        """
        temps: list[float] = []
        cvs: list[float] = []
        for line in log_contents.splitlines():
            if not line.strip():
                continue
            parts = line.split()
            try:
                temps.append(float(parts[0]))
                cvs.append(float(parts[2]))
            except (ValueError, IndexError):
                continue
        if not cvs:
            return None
        i = max(range(len(cvs)), key=cvs.__getitem__)
        return temps[i], cvs[i]

    def calculate_score(self, metrics: tuple[float, float] | None) -> float:
        if not metrics:
            return 0.0
        Tc, Cv = metrics
        if Tc is not None and Cv is not None and Cv > 0:
            return float(Tc)
        return 0.0


def create_application(app_type: str, app_config: dict[str, Any]) -> BaseApplication:
    if app_type == "monbtaw":
        return MoNbTaWApplication(app_type, app_config)
    raise ValueError(f"Unknown agenthpc application type: {app_type}")
