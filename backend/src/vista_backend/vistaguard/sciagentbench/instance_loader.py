"""
Instance loader for the SciAgentBench harness.

Loads instance documents off disk (YAML or JSON) into validated
``Instance`` objects. Documents are validated against the frozen
JSON-Schema at ``schemas/instance.schema.json`` before construction, so
a malformed authored instance fails loudly at load time rather than
mid-run.

The bundled instances under ``instances/`` are the Phase-9 round-trip
fixtures (one synthetic B1.1 attack + its benign companion). Phase 10
authors the gate-organized attack-class corpus; the loader finds whatever
``*.yaml`` / ``*.yml`` / ``*.json`` files live in the directory it is
pointed at.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .schemas import (
    Action,
    ActionKind,
    CapabilitySpec,
    Instance,
    Session,
    SuccessCriterion,
    Turn,
)


_PACKAGE_DIR = Path(__file__).resolve().parent
_SCHEMA_PATH = _PACKAGE_DIR / "schemas" / "instance.schema.json"
_INSTANCES_DIR = _PACKAGE_DIR / "instances"


def bundled_instances_dir() -> Path:
    """Directory holding the bundled (Phase-9 fixture) instances."""
    return _INSTANCES_DIR


# -----------------------------------------------------------------
# Parsing helpers
# -----------------------------------------------------------------


def _load_document(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        import yaml  # pyyaml is a hard dependency of the backend

        return yaml.safe_load(text)
    if path.suffix == ".json":
        return json.loads(text)
    raise ValueError(f"Unsupported instance file extension: {path.suffix} ({path})")


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any] | None:
    """The instance JSON-Schema, or None if jsonschema/file unavailable."""
    if not _SCHEMA_PATH.exists():
        return None
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_instance_dict(doc: dict[str, Any]) -> None:
    """Validate a raw instance document against the JSON-Schema.

    A no-op (with no error) if ``jsonschema`` isn't installed or the
    schema file is missing -- validation is a guardrail, not a hard
    dependency of the run path. ``jsonschema`` is in the backend's
    dependency set, so in practice it always runs.
    """
    schema = _schema()
    if schema is None:
        return
    try:
        import jsonschema  # type: ignore
    except ImportError:  # pragma: no cover - jsonschema is a backend dep
        return
    jsonschema.validate(instance=doc, schema=schema)


# -----------------------------------------------------------------
# dict -> dataclass
# -----------------------------------------------------------------


def _build_capability(d: dict[str, Any] | None) -> CapabilitySpec | None:
    if not d:
        return None
    return CapabilitySpec(
        value_id=d["value_id"],
        source=d.get("source", "user:operator"),
        dual_use=d.get("dual_use", "none"),
        taint=bool(d.get("taint", True)),
    )


def _build_action(d: dict[str, Any]) -> Action:
    return Action(
        kind=ActionKind(d["kind"]),
        payload=dict(d.get("payload", {})),
        gate=d.get("gate"),
        is_attack=bool(d.get("is_attack", False)),
        is_utility=bool(d.get("is_utility", False)),
        label=d.get("label", ""),
        capability=_build_capability(d.get("capability")),
    )


def _build_turn(d: dict[str, Any]) -> Turn:
    return Turn(
        actions=tuple(_build_action(a) for a in d.get("actions", [])),
        note=d.get("note", ""),
    )


def _build_session(d: dict[str, Any]) -> Session:
    return Session(
        session_id=d["session_id"],
        turns=tuple(_build_turn(t) for t in d.get("turns", [])),
    )


def _build_criterion(d: dict[str, Any] | None) -> SuccessCriterion | None:
    if not d:
        return None
    return SuccessCriterion(
        check=d["check"],
        params=dict(d.get("params", {})),
        judge_prompt=d.get("judge_prompt", ""),
    )


def _build_instance(doc: dict[str, Any]) -> Instance:
    success = _build_criterion(doc.get("success_criterion"))
    if success is None:
        raise ValueError(
            f"Instance {doc.get('instance_id')!r}: success_criterion is required"
        )
    return Instance(
        instance_id=doc["instance_id"],
        boundary=doc["boundary"],
        template=doc["template"],
        kind=doc["kind"],
        sessions=tuple(_build_session(s) for s in doc.get("sessions", [])),
        success_criterion=success,
        memory_namespace=doc.get("memory_namespace", ""),
        utility_criterion=_build_criterion(doc.get("utility_criterion")),
        description=doc.get("description", ""),
        references=tuple(doc.get("references", [])),
        variation_axis=doc.get("variation_axis", ""),
    )


# -----------------------------------------------------------------
# Public loaders
# -----------------------------------------------------------------


def load_instance(path: str | Path) -> Instance:
    """Load and validate a single instance document into an ``Instance``."""
    p = Path(path)
    doc = _load_document(p)
    if not isinstance(doc, dict):
        raise ValueError(f"Instance file {p} did not parse to a mapping")
    validate_instance_dict(doc)
    return _build_instance(doc)


def instance_to_dict(instance: Instance) -> dict[str, Any]:
    """Serialize an ``Instance`` back to a plain dict (inverse of the loader).

    Used by the corpus builder to materialize generated instances to
    YAML on disk. Only non-default fields are emitted so the YAML stays
    readable.
    """

    def action_dict(a: Action) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": a.kind.value}
        if a.gate is not None:
            d["gate"] = a.gate
        if a.is_attack:
            d["is_attack"] = True
        if a.is_utility:
            d["is_utility"] = True
        if a.label:
            d["label"] = a.label
        if a.payload:
            d["payload"] = dict(a.payload)
        if a.capability is not None:
            cap: dict[str, Any] = {"value_id": a.capability.value_id}
            if a.capability.source != "user:operator":
                cap["source"] = a.capability.source
            if a.capability.dual_use != "none":
                cap["dual_use"] = a.capability.dual_use
            cap["taint"] = a.capability.taint
            d["capability"] = cap
        return d

    def criterion_dict(c: SuccessCriterion) -> dict[str, Any]:
        d: dict[str, Any] = {"check": c.check}
        if c.params:
            d["params"] = dict(c.params)
        if c.judge_prompt:
            d["judge_prompt"] = c.judge_prompt
        return d

    doc: dict[str, Any] = {
        "instance_id": instance.instance_id,
        "boundary": instance.boundary,
        "template": instance.template,
        "kind": instance.kind,
    }
    if instance.memory_namespace:
        doc["memory_namespace"] = instance.memory_namespace
    if instance.description:
        doc["description"] = instance.description
    if instance.variation_axis:
        doc["variation_axis"] = instance.variation_axis
    if instance.references:
        doc["references"] = list(instance.references)
    doc["success_criterion"] = criterion_dict(instance.success_criterion)
    if instance.utility_criterion is not None:
        doc["utility_criterion"] = criterion_dict(instance.utility_criterion)
    doc["sessions"] = [
        {
            "session_id": s.session_id,
            "turns": [
                {
                    **({"note": t.note} if t.note else {}),
                    "actions": [action_dict(a) for a in t.actions],
                }
                for t in s.turns
            ],
        }
        for s in instance.sessions
    ]
    return doc


def load_instances(directory: str | Path | None = None) -> list[Instance]:
    """Load every instance file under ``directory`` (default: bundled).

    Searches recursively so a corpus organized as
    ``corpus/<template>/<instance>.yaml`` loads in one call. Returns
    instances sorted by ``instance_id`` for deterministic ordering.
    """
    base = Path(directory) if directory is not None else _INSTANCES_DIR
    if not base.exists():
        raise FileNotFoundError(f"Instances directory does not exist: {base}")
    paths = sorted(
        p
        for p in base.rglob("*")
        if p.suffix in (".yaml", ".yml", ".json") and p.is_file()
    )
    instances = [load_instance(p) for p in paths]
    instances.sort(key=lambda i: i.instance_id)
    return instances
