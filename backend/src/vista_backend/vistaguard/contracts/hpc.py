"""
HPC / SLURM contracts (Phase 5).

Three formal contracts that bound SLURM job submissions. This
module promotes the policy decisions to DSL `Contract` subclasses so they
live in the reviewable contract library, and the G5 fast tier consults
them through the registry instead of hardcoding the logic.

  1. ``HpcAllocationConsistencyContract`` -- the requested ``--account``
     must be one of the project's authorized allocations.
  2. ``HpcResourceCeilingContract`` -- nodes / walltime / GPUs / partition
     must stay within the matched allocation's caps.
  3. ``HpcOutputPathScopingContract`` -- the job must not touch a
     restricted filesystem location.

Each contract returns a `ContractResult` whose ``details`` carry the
fields the G5 gate needs to rebuild its incident decision unchanged:
``incident_level`` (SEV1/SEV2), ``sticky_check`` (the capability key for
the sticky-high-stakes tag), and ``sticky_metadata``. Keeping that data on
the result -- rather than building the gate's ``CapabilityTag`` here --
keeps the contracts free of gate/runtime types and independently testable.

Claim shapes::

    {"type": "hpc_allocation",  "account": "proj-x",
     "authorized": ["proj-x", "proj-y"], "enforced": True}

    {"type": "hpc_resources",   "account": "proj-x", "enforced": True,
     "nodes": 128, "time_seconds": 28800, "gpus": 8, "partition": "gpu",
     "limits": {"max_nodes": 64, "max_time_seconds": 14400,
                "max_gpus": 8, "permitted_partitions": ["batch", "gpu"]}}

    {"type": "hpc_output_path", "matched_pattern": "/etc/*", "where": "in line 3"}
    # or, self-scanning form:
    {"type": "hpc_output_path", "paths": ["~/.ssh/id_rsa"],
     "restricted_patterns": ["~/\\.ssh"]}
"""

from __future__ import annotations

import re

from .base import Contract, ContractResult, register

_SEV1 = 1
_SEV2 = 2


@register
class HpcAllocationConsistencyContract(Contract):
    """The job's allocation/account must be authorized for the project.

    No-ops (allow) when allocation enforcement is off. A missing account
    or an account outside the authorized set is a SEV2 deny, sticky on the
    ``allocation`` capability.
    """

    name = "hpc_allocation_consistency"
    domain = "hpc"
    applies_to_claims = ("hpc_allocation", "slurm_account")
    __contract_version__ = "1"

    def check(self, claim: dict) -> ContractResult:
        if not claim.get("enforced"):
            return self.passed(claim, note="allocation enforcement off")
        account = claim.get("account")
        authorized = claim.get("authorized") or []
        if account is None:
            return self.violated(
                claim,
                "G5 allocation: no --account in script and none in "
                "user_config; cannot verify against the authorized "
                "allocation list; default-deny",
                incident_level=_SEV2,
                sticky_check="allocation",
                sticky_metadata={"account": None},
            )
        if account not in authorized:
            return self.violated(
                claim,
                f"G5 allocation: account {account!r} is not an "
                f"authorized allocation {sorted(authorized)}",
                incident_level=_SEV2,
                sticky_check="allocation",
                sticky_metadata={"account": account},
            )
        return self.passed(claim, account=account)


@register
class HpcResourceCeilingContract(Contract):
    """Per-allocation SLURM resource ceilings (nodes / time / GPUs / partition).

    No-ops (allow) when enforcement is off or no allocation limits apply.
    Any axis above its cap -- or a partition outside the permitted set --
    is a SEV2 deny that G5 routes to human approval, sticky on the
    ``resource_ceiling`` capability.
    """

    name = "hpc_resource_ceiling"
    domain = "hpc"
    applies_to_claims = ("hpc_resources", "slurm_resources")
    __contract_version__ = "1"

    def check(self, claim: dict) -> ContractResult:
        limits = claim.get("limits")
        if not claim.get("enforced") or limits is None:
            return self.passed(claim, note="no allocation limits apply")

        account = claim.get("account")
        nodes = claim.get("nodes")
        time_seconds = claim.get("time_seconds")
        gpus = claim.get("gpus")
        partition = claim.get("partition")
        max_nodes = limits.get("max_nodes")
        max_time = limits.get("max_time_seconds")
        max_gpus = limits.get("max_gpus")
        permitted = limits.get("permitted_partitions") or []

        violations: list[str] = []
        if max_nodes is not None and nodes is not None and nodes > max_nodes:
            violations.append(f"nodes {nodes} > cap {max_nodes}")
        if (
            max_time is not None
            and time_seconds is not None
            and time_seconds > max_time
        ):
            violations.append(f"time {time_seconds}s > cap {max_time}s")
        if max_gpus is not None and gpus is not None and gpus > max_gpus:
            violations.append(f"gpus {gpus} > cap {max_gpus}")
        if permitted and partition is not None and partition not in permitted:
            violations.append(f"partition {partition!r} not in {sorted(permitted)}")

        if violations:
            return self.violated(
                claim,
                f"G5 resource ceiling on allocation {account!r}: "
                + "; ".join(violations),
                incident_level=_SEV2,
                sticky_check="resource_ceiling",
                sticky_metadata={"account": account, "violations": violations},
                requires_approval=True,
            )
        return self.passed(claim, account=account)


@register
class HpcOutputPathScopingContract(Contract):
    """The job must not read/write a restricted filesystem location.

    Two input forms: the G5 gate scans the script and passes the first
    ``matched_pattern`` + ``where`` it found; or, for standalone use, the
    claim carries ``paths`` and ``restricted_patterns`` and the contract
    scans them itself. A hit is a SEV1 deny, sticky on ``path_scoping``.
    """

    name = "hpc_output_path_scoping"
    domain = "hpc"
    applies_to_claims = ("hpc_output_path", "output_path", "slurm_paths")
    __contract_version__ = "1"

    def check(self, claim: dict) -> ContractResult:
        pattern = claim.get("matched_pattern")
        where = claim.get("where")
        if pattern is None:
            # Self-scanning form: search the supplied paths.
            paths = claim.get("paths") or []
            patterns = claim.get("restricted_patterns") or []
            for path in paths:
                for pat in patterns:
                    if re.search(pat, str(path)):
                        return self._deny(claim, pat, f"path {path!r}")
            return self.passed(claim)
        return self._deny(claim, pattern, where or "")

    def _deny(self, claim: dict, pattern: str, where: str) -> ContractResult:
        return self.violated(
            claim,
            f"G5 path scoping: script {where} touches restricted "
            f"location matching {pattern!r}",
            incident_level=_SEV1,
            sticky_check="path_scoping",
            sticky_metadata={"pattern": pattern},
        )
