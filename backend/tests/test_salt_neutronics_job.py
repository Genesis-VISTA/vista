"""The salt-neutronics-tbr job wrapper decodes the campaign's JSON candidate token.

The planner now passes a candidate as one JSON token (campaign.planner.encode_candidate_args),
so hpc_jobs/salt-neutronics-tbr/run_state_point.py must map that JSON onto the tbr query,
keep explicit flags taking precedence, and report keys the v1 neutronics grid can't model.
The wrapper lives outside the backend package, so load it by path.
"""

import argparse
import importlib.util
from pathlib import Path

import pytest

_WRAPPER = (
    Path(__file__).resolve().parents[2]
    / "hpc_jobs"
    / "salt-neutronics-tbr"
    / "run_state_point.py"
)


def _load_wrapper():
    spec = importlib.util.spec_from_file_location(
        "salt_neutronics_run_state_point", _WRAPPER
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _empty_args() -> argparse.Namespace:
    return argparse.Namespace(
        bef2=None,
        be_multiplier=None,
        li6=None,
        nominal_bef2=None,
        allow_extrapolation=False,
    )


def test_apply_candidate_maps_known_keys_and_reports_unmodeled():
    mod = _load_wrapper()
    args = _empty_args()
    ignored = mod.apply_candidate(
        args,
        {
            "bef2": 40,
            "li6_enrichment": 0.7,
            "temperature": 900,
            "blanket_thickness": 60,
        },
    )
    assert args.bef2 == 40.0
    assert args.li6 == 0.7
    assert sorted(ignored) == ["blanket_thickness", "temperature"]


def test_apply_candidate_synonyms_and_flags():
    mod = _load_wrapper()
    args = _empty_args()
    ignored = mod.apply_candidate(
        args, {"beryllium_multiplier": 1.1, "li6": 0.5, "allow_extrapolation": True}
    )
    assert args.be_multiplier == 1.1
    assert args.li6 == 0.5
    assert args.allow_extrapolation is True
    assert ignored == []


def test_apply_candidate_does_not_override_explicit_flags():
    mod = _load_wrapper()
    args = _empty_args()
    args.li6 = 0.075  # set on the command line
    mod.apply_candidate(args, {"li6_enrichment": 0.9})
    assert args.li6 == 0.075  # the flag wins over the JSON


def test_main_rejects_non_object_candidate():
    mod = _load_wrapper()
    with pytest.raises(SystemExit):
        mod.main(["--skill-root", "/x", "--output-dir", "/y", "[1, 2, 3]"])
