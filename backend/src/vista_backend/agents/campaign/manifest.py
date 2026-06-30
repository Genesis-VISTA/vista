"""
Campaign manifest — the `campaign.yaml` that lives inside a planner skill.

This is the single, skill-native place a campaign is defined (see
docs/multi-agent-framework.md): the design variables, the goal metric (+ the
scorer script the planner runs), and the subagent roles with their sim-skill and
HPC-job bindings. The backend reads it to wire the planner + subagents; it adds no
domain code of its own.
"""
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


CAMPAIGN_MANIFEST_FILENAME = "campaign.yaml"


class CampaignVariable(BaseModel):
    name: str
    range: tuple[float, float] | None = None
    unit: str | None = None


class CampaignMetric(BaseModel):
    name: str
    target: float | None = None
    direction: Literal["maximize", "minimize"] = "maximize"


class CampaignMetrics(BaseModel):
    primary: CampaignMetric
    scorer: str | None = None
    """ Path (relative to the planner skill dir) of the deterministic scorer script. """


class SubagentSpec(BaseModel):
    """Binds a subagent role to the sim skill that specializes it and the HPC job it submits."""
    role: str
    skill: str
    job: str
    default_count: int = 1
    """ Default number of instances per candidate; a default, not a cap (the planner may fan out more). """
    result_files: list[str] = Field(default_factory=lambda: ["results.json"])
    """ Output files the subagent fetches from a finished job to parse its result. The monitor's
    collector reads these (the convention is a single results.json the job writes). """


class CampaignSearch(BaseModel):
    strategy: str = "bayesian"


class CampaignManifest(BaseModel):
    domain: str
    variables: list[CampaignVariable] = Field(default_factory=list)
    metrics: CampaignMetrics
    subagents: list[SubagentSpec] = Field(default_factory=list)
    search: CampaignSearch = Field(default_factory=CampaignSearch)

    def subagent(self, role: str) -> SubagentSpec | None:
        return next((s for s in self.subagents if s.role == role), None)

    @property
    def roles(self) -> list[str]:
        return [s.role for s in self.subagents]


def load_manifest(planner_skill_dir: Path | str) -> CampaignManifest:
    """Load + validate the `campaign.yaml` from a planner skill directory."""
    path = Path(planner_skill_dir) / CAMPAIGN_MANIFEST_FILENAME
    if not path.exists():
        raise FileNotFoundError(
            f"No {CAMPAIGN_MANIFEST_FILENAME} found in planner skill {planner_skill_dir}"
        )
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a YAML mapping")
    return CampaignManifest.model_validate(data)
