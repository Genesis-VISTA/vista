"""
VISTAGuard contract library (Phase 5).

The neuro-symbolic "hard constraint" layer: deterministic, model-free
checks that bound scientific claims the agent emits. Public surface:

  * `Contract`, `ContractResult` -- the DSL.
  * `register`, `default_registry`, `ContractRegistry` -- registration.
  * `load_contract_library` -- the loader the sidecar calls.

This package also contains non-Python contract data used by other gates
(the `semgrep/` ruleset for G4 and `jailbreak_signatures.txt` for G1);
those are read by path and are unaffected by this package's exports.
"""

from __future__ import annotations

from .base import (
    Contract,
    ContractRegistry,
    ContractResult,
    claim_type,
    default_registry,
    register,
)
from .enforcement import (
    ContractCheckOutcome,
    enforce,
    evaluate_claims,
    extract_claims,
)
from .loader import load_contract_library

__all__ = [
    "Contract",
    "ContractRegistry",
    "ContractResult",
    "ContractCheckOutcome",
    "claim_type",
    "default_registry",
    "register",
    "load_contract_library",
    "enforce",
    "evaluate_claims",
    "extract_claims",
]
