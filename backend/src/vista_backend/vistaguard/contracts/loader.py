"""
Contract library loader (Phase 5).

`load_contract_library` returns a populated `ContractRegistry`. It always
loads the builtin contracts (salts, spectroscopy, hpc) by importing their
modules, which registers them via the `@register` decorator into the
default registry. If an operator-supplied contracts directory exists
(`settings.contracts_dir`), each `*.py` file in it is imported too, so
scientist-contributed contracts land in the same registry without touching
the VISTAGuard runtime.

The loader is import-safe and idempotent: builtin modules import once and
registration is keyed by contract name, so repeated calls (one per sidecar)
do not duplicate contracts.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
from pathlib import Path

from .base import ContractRegistry, default_registry

logger = logging.getLogger(__name__)

# Builtin contract modules, imported for their @register side effects.
_BUILTIN_MODULES = (
    "vista_backend.vistaguard.contracts.salts",
    "vista_backend.vistaguard.contracts.hpc",
)


def _load_builtins() -> None:
    for mod in _BUILTIN_MODULES:
        importlib.import_module(mod)


def _load_external(registry: ContractRegistry, contracts_dir: str) -> None:
    root = Path(contracts_dir)
    if not root.is_dir():
        logger.debug("VISTAGuard contracts_dir %s not present; skipping", root)
        return
    for path in sorted(root.glob("*.py")):
        if path.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(
            f"vistaguard_external_contracts.{path.stem}", path
        )
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception:  # pragma: no cover - operator code, log and continue
            logger.exception("Failed to load external contract %s", path)


def load_contract_library(contracts_dir=None, project=None) -> ContractRegistry:
    """Discover and return the contract registry at startup.

    Always loads the builtin contracts (salts, spectroscopy, hpc). When
    `contracts_dir` is given and exists, every non-underscore ``*.py``
    file in it is imported too, so scientist-contributed contracts land
    in the same registry without touching the VISTAGuard runtime.

    `contracts_dir` may be a path string / `Path` (typically
    `settings.contracts_dir`); a settings-like object carrying a
    ``contracts_dir`` attribute is also accepted for convenience.
    `project` is accepted for future per-project contract scoping and is
    currently unused.

    Idempotent: builtin modules import once and registration is keyed by
    contract name, so repeated calls do not duplicate contracts.
    """
    _load_builtins()
    registry = default_registry()
    # Accept either a path or a settings-like object with `.contracts_dir`.
    if contracts_dir is not None and not isinstance(contracts_dir, (str, Path)):
        contracts_dir = getattr(contracts_dir, "contracts_dir", None)
    if contracts_dir:
        _load_external(registry, contracts_dir)
    return registry
