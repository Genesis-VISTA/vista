"""
Campaign manifest — the `campaign.yaml` that lives inside a planner skill.

This is the single, skill-native place a campaign is defined (see
docs/multi-agent-framework.md): the design variables, the goal metric (+ the
scorer script the planner runs), and the subagent roles with their sim-skill and
HPC-job bindings. The backend reads it to wire the planner + subagents; it adds no
domain code of its own.
"""

import json
from pathlib import Path
from typing import Any, Literal

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


class SubagentArgs(BaseModel):
    """
    How a candidate becomes this role's `script_args`.

    Real `hpc_jobs/` wrappers parse flat CLI flags with argparse, so `flags` renders
    `<flag> <value>` per mapped variable. The mapping is per-role because one candidate
    feeds roles that need different subsets under different flag names.
    """

    encoding: Literal["flags", "json"] = "flags"
    map: dict[str, str] = Field(default_factory=dict)
    """ Candidate variable name -> CLI flag, e.g. {"li6_enrichment": "--li6"}. """
    extra: str = ""
    """ Fixed, non-candidate arguments appended verbatim, e.g. "--allow-extrapolation". """


class SubagentSpec(BaseModel):
    """Binds a subagent role to the sim skill that specializes it and the HPC job it submits."""

    role: str
    skill: str
    job: str
    default_count: int = 1
    """ Default number of instances per candidate; a default, not a cap (the planner may fan out more). """
    args: SubagentArgs | None = None
    """
    Candidate -> script_args encoding. `None` (the default) preserves the pre-contract
    behavior of serializing the whole candidate as JSON, so manifests that have not opted
    in are untouched.
    """
    collect_files: list[str] = Field(default_factory=list)
    """ Job output files the result parser needs, e.g. ["results.json"]. Empty = fetch none. """


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


def _format_value(value: Any) -> str:
    """Render one candidate value for a CLI flag; bools become argparse store_true style."""
    if isinstance(value, bool):
        return ""  # caller drops the value: `--flag` alone, or omits it entirely
    return str(value)


def render_script_args(
    manifest: CampaignManifest, spec: SubagentSpec, candidate: dict | None
) -> str | None:
    """
    Encode `candidate` into the `script_args` string for `spec`'s job.

    Pure: no I/O, no DB. `spec.args is None` reproduces the pre-contract behavior
    (the whole candidate as JSON) so manifests that have not opted in are unchanged.

    With `encoding: flags`, mapped variables render as `<flag> <value>` in the manifest's
    *variable declaration order* — deterministic, so the output is assertable and job
    logs stay diffable. Candidate keys absent from `spec.args.map` are not passed.
    Candidate keys the manifest never declared as variables are appended afterwards, in
    candidate order, so a planner-supplied extra still reaches the job if it is mapped.
    """
    if spec.args is None:
        # Pre-contract default: serialize the candidate, or pass nothing when empty.
        return json.dumps(candidate) if candidate else None

    if spec.args.encoding == "json":
        parts = [json.dumps(candidate)] if candidate else []
        if spec.args.extra:
            parts.append(spec.args.extra)
        return " ".join(parts) or None

    candidate = candidate or {}
    declared = [v.name for v in manifest.variables]
    ordered = [k for k in declared if k in candidate]
    ordered += [k for k in candidate if k not in declared]

    tokens: list[str] = []
    for name in ordered:
        flag = spec.args.map.get(name)
        if not flag:
            continue
        value = candidate[name]
        if isinstance(value, bool):
            if value:
                tokens.append(flag)  # store_true style: presence is the value
            continue
        tokens.append(flag)
        tokens.append(_format_value(value))

    rendered = " ".join(tokens)
    if spec.args.extra:
        rendered = f"{rendered} {spec.args.extra}".strip()
    return rendered or None


def load_manifest(planner_skill_dir: Path | str) -> CampaignManifest:
    """Load + validate the `campaign.yaml` from a planner skill directory."""
    path = Path(planner_skill_dir) / CAMPAIGN_MANIFEST_FILENAME
    if not path.exists():
        raise FileNotFoundError(
            f"No {CAMPAIGN_MANIFEST_FILENAME} found in planner skill {planner_skill_dir}"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a YAML mapping")
    return CampaignManifest.model_validate(data)
