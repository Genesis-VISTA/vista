"""
G5 HPC Job Gate -- fast-tier policy checks on resolved SLURM jobs.

G5 is the data-in / decision-out core gate for HPC job submission. Like
:class:`~vista_backend.vistaguard.gates.g4_code.G4CodeGate` it is fully
unit-testable without a running agent: ``check_fast`` takes a payload
``{"slurm_script": str, "user_config": dict}`` and returns a
:class:`~vista_backend.vistaguard.gates.base.GateDecision`. How the gate
is wired into PydanticAI (the ``submit_hpc_job`` / ``cancel_hpc_job`` /
``run_bash``-SSH hooks) is the separate ``G5HpcCapability`` work item;
the Q-LLM intent extraction + chained-job DAG walk are the separate
slow-tier work item. This module ships only the deterministic fast tier
plus the :class:`AllocationPolicy` schema both later issues build on.

## The six fast-tier checks (first deny wins)

1. **Allocation / account allow-list.** The job's ``--account`` (or the
   account in ``user_config``) must be one of the project's authorized
   allocations. Unauthorized account -> **SEV2** deny.
2. **Resource ceilings.** ``--nodes`` / ``--time`` / ``--gpus`` and the
   ``--partition`` are checked against the matched allocation's caps.
   Above-ceiling -> **SEV2** deny, and the decision carries a *sticky*
   capability tag (the request does not auto-recover within the
   session; see ``sticky_high_stakes`` in config).
3. **Binary denylist / mining IOC.** Every invoked binary is checked
   against the mining-binary denylist, and the whole script is scanned
   for mining signatures (pool URLs, RandomX/CryptoNight markers).
   Match -> **SEV1** deny.
4. **Path scoping.** A script that touches a sensitive filesystem
   location (``~/.ssh``, ``/etc/``, ``~/.aws``, private-key files) ->
   **SEV1** deny.
5. **Network egress.** Outbound ``curl`` / ``wget`` / ``scp`` to a host
   not on the allow-list -> **SEV2** deny.
6. **Credential exfiltration.** A script that reads ``~/.netrc``,
   ``~/.git-credentials``, or a VISTA / cloud credential env-var with
   no legitimate use -> **SEV1** deny.

In addition to the six allow-/deny-list checks, the fast tier denies
**opaque code execution** (SEV2): decoding embedded content straight
into a shell (``base64 -d | bash``) or ``eval``-ing the output of a
remote fetch (``eval $(curl ...)``). The fast tier cannot allow-list a
binary it cannot see, so these B5.1 *delivery* shapes -- surfaced by
the parser's multi-stage detection -- are denied structurally; the
opaque payload itself is what the slow-tier Q-LLM (separate work item)
would otherwise have to reason about. Download-then-exec is *not* denied
on structure alone (a job legitimately fetching a dataset from an
allow-listed host then running a local script is benign); its network
fetch is instead caught by the egress check when the host is untrusted.

Default-deny posture: a malformed payload raises ``TypeError`` (a
caller bug), and a script the parser chokes on -> **SEV2** deny rather
than fall through to allow, matching the rest of VISTAGuard.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from ..capabilities import CapabilityTag, DualUseMarker
from ..quarantine import (
    HIGH_STAKES_CODE_CATEGORIES,
    CodeIntentExtraction,
    run_code_intent_extraction_with_self_consistency,
)
from ..contracts import ContractRegistry, load_contract_library
from .base import Gate, GateContext, GateDecision
from .g4_code import _detect_intent_mismatch, _read_g1_intent
from .slurm_parser import (
    MULTISTAGE_DECODE_PIPE_SHELL,
    MULTISTAGE_EVAL_SUBSTITUTION,
    SlurmScript,
    parse_slurm_script,
)

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------
# Incident severities (mirrors G4: SEV1=1 most severe, SEV2=2, SEV3=3)
# -----------------------------------------------------------------

_SEV1 = 1
_SEV2 = 2


# -----------------------------------------------------------------
# Slow-tier defaults
# -----------------------------------------------------------------

# Below this Q-LLM confidence the slow tier default-denies SEV2 (an
# incoherent intent on an HPC job is the load-bearing obfuscation signal).
# Matches G1 / G4.
_DEFAULT_JOB_INTENT_CONFIDENCE_THRESHOLD: float = 0.5

# At or above this confidence, a *weapons-relevant* dual-use marker
# (CHEM / BIO / NUCLEAR) denies SEV1 + sticky. CYBER dual-use is left to
# the intent-mismatch comparator (SEV2).
_DEFAULT_DUAL_USE_HIGH_CONFIDENCE_THRESHOLD: float = 0.8

# Dual-use markers treated as weapons-relevant for the SEV1 path.
_WEAPONS_DUAL_USE: frozenset[DualUseMarker] = frozenset(
    {DualUseMarker.CHEM, DualUseMarker.BIO, DualUseMarker.NUCLEAR}
)

# Bound on the chained-job dependency walk (depth + total nodes) so a
# cyclic or adversarially-deep --dependency graph can't wedge the gate.
_DEFAULT_DAG_MAX_DEPTH: int = 8
_DEFAULT_DAG_MAX_NODES: int = 64

# A resolver maps a SLURM dependency reference (a JOBID or job name) to
# the resolved script text, or None when it can't be resolved.
JobScriptResolver = Callable[[str], "str | None"]


# -----------------------------------------------------------------
# Bundled defaults
# -----------------------------------------------------------------


ALLOCATION_POLICY_FILENAME = "g5_allocation_policy.json"
"""Default filename for the operator-supplied allocation policy."""

ALLOCATION_POLICY_VERSION = 1


# Public mining-binary IOC list. Sourced from public threat-intel feeds
# (Cado Security / Sysdig cloud-cryptomining reports, the abuse.ch
# feodotracker miner family list, and the well-known XMRig/ethminer
# family). >= 10 entries per the work-item acceptance criterion; the
# operator file augments rather than replaces this set.
DEFAULT_MINING_BINARY_DENYLIST: frozenset[str] = frozenset(
    {
        "xmrig",
        "xmr-stak",
        "xmr-stak-rx",
        "ethminer",
        "phoenixminer",
        "t-rex",
        "trex",
        "nbminer",
        "lolminer",
        "gminer",
        "teamredminer",
        "cpuminer",
        "cpuminer-multi",
        "minerd",
        "cgminer",
        "bfgminer",
        "ccminer",
        "srbminer",
        "srbminer-multi",
        "nanominer",
        "bminer",
        "miniz",
        "kawpowminer",
        "verthashminer",
        "wildrig",
        "xmrig-cuda",
    }
)

# Substring signatures (lower-cased) that betray a mining payload even
# when the binary itself has been renamed -- pool protocol URIs,
# proof-of-work algorithm markers, and the dominant public Monero pools.
# These catch the "library-call mining" pattern where no denylisted
# binary name appears.
DEFAULT_MINING_SIGNATURES: tuple[str, ...] = (
    "stratum+tcp://",
    "stratum+ssl://",
    "stratum2+tcp://",
    "randomx",
    "cryptonight",
    "argon2id_chukwa",
    "minexmr",
    "supportxmr.com",
    "nanopool.org",
    "pool.minexmr.com",
    "xmrpool",
    "nicehash.com",
    "ethermine.org",
    "f2pool.com",
    "2miners.com",
    "--donate-level",
    "--coin=monero",
)

# Host allow-list for the network-egress check. fnmatch globs. Covers
# the OLCF / NERSC / OSTI infrastructure VISTA's HPC dispatchers already
# talk to (Odo via S3M at OLCF, Perlmutter via IRI at NERSC), plus
# loopback for in-allocation traffic.
DEFAULT_HOST_ALLOW_LIST: tuple[str, ...] = (
    "*.olcf.ornl.gov",
    "*.ornl.gov",
    "*.nersc.gov",
    "*.osti.gov",
    "localhost",
    "127.0.0.1",
    "::1",
)


# Sensitive filesystem locations for the path-scoping check (check 4).
# Matched as substrings against path tokens (and the raw script) so
# ``$HOME/.ssh/id_rsa`` and ``~/.ssh/id_rsa`` both fire.
DEFAULT_SENSITIVE_PATH_PATTERNS: tuple[str, ...] = (
    "~/.ssh",
    "$HOME/.ssh",
    "/.ssh/",
    "~/.aws",
    "$HOME/.aws",
    "/.aws/",
    "/.config/gcloud",
    "/etc/passwd",
    "/etc/shadow",
    "/etc/sudoers",
    "/root/",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
)

# Credential-exfiltration markers for check 6: credential *files* and
# secret-bearing env vars that a benign molten-salt training job never
# touches. ``~/.ssh`` lives in the path-scoping check (which fires
# first); this set is the netrc / token-leak surface.
DEFAULT_CREDENTIAL_PATTERNS: tuple[str, ...] = (
    "~/.netrc",
    "$HOME/.netrc",
    "/.netrc",
    "~/.git-credentials",
    "/.git-credentials",
    "_netrc",
    "$VISTA_MCP_S3M_TOKEN",
    "VISTA_MCP_S3M_TOKEN",
    "VISTA_MCP_NERSC_IRI_TOKEN",
    "NERSC_IRI_TOKEN",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
    "HF_TOKEN",
    "HUGGINGFACE_TOKEN",
)


# -----------------------------------------------------------------
# AllocationPolicy schema
# -----------------------------------------------------------------


@dataclass(frozen=True)
class AllocationLimits:
    """
    Per-allocation resource caps. ``None`` means "no cap on this axis."
    ``permitted_partitions`` empty means "any partition allowed."
    """

    max_nodes: int | None = None
    max_time_seconds: int | None = None
    max_gpus: int | None = None
    permitted_partitions: frozenset[str] = frozenset()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AllocationLimits":
        parts = data.get("permitted_partitions") or []
        if not isinstance(parts, (list, tuple, set, frozenset)):
            parts = []
        return cls(
            max_nodes=_opt_int(data.get("max_nodes")),
            max_time_seconds=_opt_int(data.get("max_time_seconds")),
            max_gpus=_opt_int(data.get("max_gpus")),
            permitted_partitions=frozenset(str(p) for p in parts),
        )


@dataclass(frozen=True)
class AllocationPolicy:
    """
    Operator-supplied G5 policy: the authorized allocations and their
    caps, the mining-binary denylist, and the network host allow-list.

    The schema mirrors the ``g5_allocation_policy.json`` documented in
    the work item. :meth:`from_dict` parses one (tolerating missing
    sections); :meth:`default` returns the bundled-defaults policy with
    *no* allocations -- which, by :meth:`allocations_enforced`, means
    the allocation + ceiling checks are skipped until an operator
    supplies a real allocation map, while the binary / path / network /
    credential checks still run with bundled defaults.

    File loading (``load_allocation_policy(path)``) is the separate
    settings work item; this class is the schema both it and the gate
    depend on.
    """

    version: int = ALLOCATION_POLICY_VERSION
    allocations: Mapping[str, AllocationLimits] = field(default_factory=dict)
    binary_denylist: frozenset[str] = DEFAULT_MINING_BINARY_DENYLIST
    host_allow_list: tuple[str, ...] = DEFAULT_HOST_ALLOW_LIST
    loaded_from: Path | None = None

    @classmethod
    def default(cls) -> "AllocationPolicy":
        """Bundled-defaults policy (no allocation enforcement)."""
        return cls()

    @property
    def allocations_enforced(self) -> bool:
        """
        True when an allocation map was supplied. When False the
        allocation + ceiling checks no-op (allow) -- the safe default
        for a deployment that hasn't authored a policy file yet.
        """
        return bool(self.allocations)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AllocationPolicy":
        """
        Parse an allocation-policy mapping. The bundled mining denylist
        and host allow-list are *augmented* (unioned) by the file's
        ``binary_denylist`` / ``host_allow_list`` rather than replaced,
        so an operator file can only ever tighten the IOC coverage.

        Raises ``ValueError`` on a wrong/missing version so the caller
        (the file loader) can apply the documented "wrong version ->
        bundled defaults, WARNING logged" fallback. Malformed *entries*
        are skipped with a warning rather than aborting the whole parse.
        """
        if not isinstance(data, Mapping):
            raise ValueError("allocation policy must be a JSON object")
        if data.get("version") != ALLOCATION_POLICY_VERSION:
            raise ValueError(
                f"allocation policy version {data.get('version')!r}; "
                f"expected {ALLOCATION_POLICY_VERSION}"
            )

        allocations: dict[str, AllocationLimits] = {}
        for name, limits in (data.get("allocations") or {}).items():
            if not isinstance(name, str) or not isinstance(limits, Mapping):
                logger.warning(
                    "VISTAGuard G5: skipping malformed allocation entry "
                    "%r=%r",
                    name, limits,
                )
                continue
            allocations[name] = AllocationLimits.from_dict(limits)

        file_denylist = {
            str(b).lower()
            for b in (data.get("binary_denylist") or [])
            if isinstance(b, str)
        }
        file_hosts = tuple(
            str(h)
            for h in (data.get("host_allow_list") or [])
            if isinstance(h, str)
        )

        return cls(
            version=ALLOCATION_POLICY_VERSION,
            allocations=allocations,
            binary_denylist=DEFAULT_MINING_BINARY_DENYLIST | file_denylist,
            host_allow_list=DEFAULT_HOST_ALLOW_LIST + file_hosts,
        )


def _opt_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# -----------------------------------------------------------------
# G5HpcJobGate
# -----------------------------------------------------------------


class G5HpcJobGate(Gate):
    """
    G5 fast-tier HPC job-submission gate. See the module docstring for
    the six checks.
    """

    name = "G5"

    def __init__(
        self,
        *,
        enabled: bool = True,
        allocation_policy: AllocationPolicy | None = None,
        binary_allow_list: frozenset[str] | None = None,
        mining_signatures: Sequence[str] = DEFAULT_MINING_SIGNATURES,
        sensitive_path_patterns: Sequence[str] = DEFAULT_SENSITIVE_PATH_PATTERNS,
        credential_patterns: Sequence[str] = DEFAULT_CREDENTIAL_PATTERNS,
        # Slow-tier configuration. The code-intent agent is supplied by the
        # sidecar at build time (G4 and G5 share it -- the prompt shape is
        # "what is this code trying to do?").
        code_intent_extraction_agent: Any | None = None,
        code_intent_confidence_threshold: float = _DEFAULT_JOB_INTENT_CONFIDENCE_THRESHOLD,
        code_intent_self_consistency_samples: int = 1,
        dual_use_high_confidence_threshold: float = _DEFAULT_DUAL_USE_HIGH_CONFIDENCE_THRESHOLD,
        high_stakes_code_categories: frozenset[str] | None = None,
        weapons_dual_use_markers: frozenset[DualUseMarker] | None = None,
        chained_job_dag_enabled: bool = True,
        dag_max_depth: int = _DEFAULT_DAG_MAX_DEPTH,
        dag_max_nodes: int = _DEFAULT_DAG_MAX_NODES,
        contract_registry: ContractRegistry | None = None,
    ) -> None:
        super().__init__(enabled=enabled)
        # The fast-tier allocation / resource-ceiling / path-scoping
        # checks are DSL contracts consulted through the registry. Default
        # to the builtin library (idempotent load) when none is injected.
        self._contracts = (
            contract_registry
            if contract_registry is not None
            else load_contract_library()
        )
        self._policy = (
            allocation_policy
            if allocation_policy is not None
            else AllocationPolicy.default()
        )
        self._binary_allow_list = (
            frozenset(binary_allow_list)
            if binary_allow_list is not None
            else None
        )
        self._mining_signatures = tuple(s.lower() for s in mining_signatures)
        self._sensitive_path_patterns = tuple(sensitive_path_patterns)
        self._credential_patterns = tuple(credential_patterns)
        self._code_intent_extraction_agent = code_intent_extraction_agent
        self._code_intent_confidence_threshold = code_intent_confidence_threshold
        self._code_intent_self_consistency_samples = max(
            1, int(code_intent_self_consistency_samples)
        )
        self._dual_use_high_confidence_threshold = dual_use_high_confidence_threshold
        self._high_stakes_code_categories: frozenset[str] = (
            high_stakes_code_categories
            if high_stakes_code_categories is not None
            else HIGH_STAKES_CODE_CATEGORIES
        )
        self._weapons_dual_use: frozenset[DualUseMarker] = (
            weapons_dual_use_markers
            if weapons_dual_use_markers is not None
            else _WEAPONS_DUAL_USE
        )
        self._chained_job_dag_enabled = chained_job_dag_enabled
        self._dag_max_depth = dag_max_depth
        self._dag_max_nodes = dag_max_nodes

    # -----------------------------------------------------------------
    # Read-only views
    # -----------------------------------------------------------------

    @property
    def policy(self) -> AllocationPolicy:
        return self._policy

    @property
    def binary_allow_list(self) -> frozenset[str] | None:
        return self._binary_allow_list

    @property
    def code_intent_extraction_agent(self) -> Any | None:
        return self._code_intent_extraction_agent

    @property
    def code_intent_confidence_threshold(self) -> float:
        return self._code_intent_confidence_threshold

    @property
    def chained_job_dag_enabled(self) -> bool:
        return self._chained_job_dag_enabled

    @property
    def high_stakes_code_categories(self) -> frozenset[str]:
        return self._high_stakes_code_categories

    def attach_code_intent_extraction_agent(self, agent: Any) -> None:
        """Store the shared code-intent Q-LLM agent for the slow tier."""
        self._code_intent_extraction_agent = agent

    # -----------------------------------------------------------------
    # Fast-tier check
    # -----------------------------------------------------------------

    async def _check_fast_when_enabled(
        self,
        payload: Any,
        ctx: GateContext,
    ) -> GateDecision:
        if not isinstance(payload, dict):
            raise TypeError(
                "G5HpcJobGate expected payload dict with "
                "'slurm_script'/'user_config'; got "
                f"{type(payload).__name__}"
            )
        slurm_script = payload.get("slurm_script")
        user_config = payload.get("user_config", {})
        if not isinstance(slurm_script, str):
            raise TypeError(
                "G5HpcJobGate payload must carry a string 'slurm_script'; "
                f"got {type(slurm_script).__name__}"
            )
        if not isinstance(user_config, dict):
            raise TypeError(
                "G5HpcJobGate payload 'user_config' must be a dict; got "
                f"{type(user_config).__name__}"
            )

        if not slurm_script.strip():
            return GateDecision(allow=True, reason="G5: empty SLURM script")

        # Parse defensively: an unparseable script default-denies SEV2.
        try:
            script = parse_slurm_script(slurm_script)
        except Exception as exc:  # pragma: no cover - parser is no-raise
            logger.warning(
                "VISTAGuard G5: SLURM parse failed (%s: %s); default-deny",
                type(exc).__name__, exc,
            )
            return GateDecision(
                allow=False,
                reason=(
                    f"G5: SLURM parse failed "
                    f"({type(exc).__name__}: {exc}); default-deny"
                ),
                incident_level=_SEV2,
            )

        deny = self._run_fast_checks(script, user_config)
        if deny is not None:
            return deny

        return GateDecision(
            allow=True,
            reason=(
                f"G5: fast-tier ok "
                f"(account={self._resolve_account(script, user_config)!r}, "
                f"binaries={list(script.binaries)[:6]})"
            ),
        )

    def _run_fast_checks(
        self, script: SlurmScript, user_config: Mapping[str, Any]
    ) -> GateDecision | None:
        """
        Run the six fast-tier checks (plus the opaque-execution check) in
        first-deny-wins order, returning the first deny `GateDecision` or
        None when every check passes.

        Factored out of ``_check_fast_when_enabled`` so the chained-job
        DAG walker can re-apply the identical checks to each dependent
        node (a node is a parsed `SlurmScript`, so the walker skips the
        payload-validation / parse preamble).
        """
        for check in (
            self._check_allocation,
            self._check_resource_ceilings,
            self._check_mining_binaries,
            self._check_obfuscated_execution,
            self._check_path_scoping,
            self._check_network_egress,
            self._check_credential_exfiltration,
        ):
            decision = check(script, user_config)
            if decision is not None:
                return decision
        return None

    # -----------------------------------------------------------------
    # Fast-tier decision summary (for the approval UI)
    # -----------------------------------------------------------------

    def fast_tier_metadata(
        self, slurm_script: str, user_config: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Build a structured summary of the fast-tier checks for a script
        that has already passed them, for display in the human-approval
        UI ("account verified, resource ceiling passed, no denylist
        match"). Best-effort: a parse failure yields a minimal dict with
        the raw script. This is presentation metadata, not an enforcement
        decision -- the gate's own ``check_fast`` is the source of truth.
        """
        user_config = user_config or {}
        meta: dict[str, Any] = {"gate": "G5", "resolved_script": slurm_script}
        try:
            script = parse_slurm_script(slurm_script)
        except Exception:  # pragma: no cover - parser is no-raise
            return meta

        account = self._resolve_account(script, user_config)
        account_verified = (
            (account in self._policy.allocations)
            if self._policy.allocations_enforced
            else None
        )
        nodes = _coalesce_int(script.directives.nodes, user_config.get("node_count"))
        time_seconds = _coalesce_int(
            script.directives.time_seconds, user_config.get("duration_seconds")
        )
        meta.update(
            {
                "account": account,
                "account_verified": account_verified,
                "allocation_enforced": self._policy.allocations_enforced,
                "requested_nodes": nodes,
                "requested_time_seconds": time_seconds,
                "requested_gpus": script.directives.total_gpus,
                "partition": script.directives.partition,
                "resource_ceiling_passed": True,
                "binary_denylist_match": None,
                "invoked_binaries": list(script.binaries)[:20],
                "network_targets": list(script.network_targets)[:20],
                "checks_passed": [
                    "allocation",
                    "resource_ceiling",
                    "binary_denylist",
                    "path_scoping",
                    "network_egress",
                    "credential_exfiltration",
                ],
            }
        )
        return meta

    # -----------------------------------------------------------------
    # Check 1 -- allocation / account allow-list
    # -----------------------------------------------------------------

    def _resolve_account(
        self, script: SlurmScript, user_config: Mapping[str, Any]
    ) -> str | None:
        if script.directives.account:
            return script.directives.account
        for key in ("account", "hpc_account", "nersc_account"):
            val = user_config.get(key)
            if isinstance(val, str) and val:
                return val
        return None

    def _decision_from_contract(
        self, contract_name: str, claim: Mapping[str, Any]
    ) -> GateDecision | None:
        """
        Run a registry contract over `claim` and translate a violation
        into the gate's `GateDecision` (preserving the incident level and
        sticky-capability tag the contract reports). Returns None when the
        contract passes or is not registered.
        """
        contract = self._contracts.get(contract_name)
        if contract is None:  # pragma: no cover - library always loaded
            logger.warning(
                "VISTAGuard G5: contract %r not in registry; skipping",
                contract_name,
            )
            return None
        result = contract.check(dict(claim))
        if result.ok:
            return None
        details = result.details
        sticky = details.get("sticky_check")
        return GateDecision(
            allow=False,
            reason=result.reason,
            incident_level=details.get("incident_level", _SEV2),
            capability_tag=(
                _sticky_tag(sticky, details.get("sticky_metadata", {}))
                if sticky
                else None
            ),
        )

    def _check_allocation(
        self, script: SlurmScript, user_config: Mapping[str, Any]
    ) -> GateDecision | None:
        return self._decision_from_contract(
            "hpc_allocation_consistency",
            {
                "type": "hpc_allocation",
                "account": self._resolve_account(script, user_config),
                "authorized": sorted(self._policy.allocations),
                "enforced": self._policy.allocations_enforced,
            },
        )

    # -----------------------------------------------------------------
    # Check 2 -- resource ceilings (above-ceiling is sticky SEV2)
    # -----------------------------------------------------------------

    def _check_resource_ceilings(
        self, script: SlurmScript, user_config: Mapping[str, Any]
    ) -> GateDecision | None:
        account = self._resolve_account(script, user_config)
        limits = (
            self._policy.allocations.get(account or "")
            if self._policy.allocations_enforced
            else None
        )
        return self._decision_from_contract(
            "hpc_resource_ceiling",
            {
                "type": "hpc_resources",
                "account": account,
                "enforced": self._policy.allocations_enforced,
                "nodes": _coalesce_int(
                    script.directives.nodes, user_config.get("node_count")
                ),
                "time_seconds": _coalesce_int(
                    script.directives.time_seconds,
                    user_config.get("duration_seconds"),
                ),
                "gpus": script.directives.total_gpus,
                "partition": script.directives.partition,
                "limits": None
                if limits is None
                else {
                    "max_nodes": limits.max_nodes,
                    "max_time_seconds": limits.max_time_seconds,
                    "max_gpus": limits.max_gpus,
                    "permitted_partitions": sorted(limits.permitted_partitions),
                },
            },
        )

    # -----------------------------------------------------------------
    # Check 3 -- mining-binary denylist + signatures (SEV1)
    # -----------------------------------------------------------------

    def _check_mining_binaries(
        self, script: SlurmScript, user_config: Mapping[str, Any]
    ) -> GateDecision | None:
        denylist = self._policy.binary_denylist
        for binary in script.binaries:
            if binary.lower() in denylist:
                return GateDecision(
                    allow=False,
                    reason=(
                        f"G5 mining-binary denylist: invoked binary "
                        f"{binary!r} matches a known mining IOC"
                    ),
                    incident_level=_SEV1,
                    capability_tag=_sticky_tag(
                        "mining_binary", {"binary": binary}
                    ),
                )

        # Optional allow-list mode: when configured, any invoked binary
        # not on the allow-list denies SEV1 (default-deny).
        if self._binary_allow_list is not None:
            for binary in script.binaries:
                if binary.lower() not in self._binary_allow_list:
                    return GateDecision(
                        allow=False,
                        reason=(
                            f"G5 binary allow-list: invoked binary "
                            f"{binary!r} is not on the allow-list"
                        ),
                        incident_level=_SEV1,
                    )

        lowered = script.raw.lower()
        for sig in self._mining_signatures:
            if sig in lowered:
                return GateDecision(
                    allow=False,
                    reason=(
                        f"G5 mining signature: script contains the "
                        f"mining marker {sig!r}"
                    ),
                    incident_level=_SEV1,
                    capability_tag=_sticky_tag(
                        "mining_signature", {"signature": sig}
                    ),
                )
        return None

    # -----------------------------------------------------------------
    # Opaque code execution (SEV2) -- decode-pipe-to-shell / eval-fetch
    # -----------------------------------------------------------------

    def _check_obfuscated_execution(
        self, script: SlurmScript, user_config: Mapping[str, Any]
    ) -> GateDecision | None:
        flags = script.multistage_flags
        if MULTISTAGE_DECODE_PIPE_SHELL in flags:
            return GateDecision(
                allow=False,
                reason=(
                    "G5 opaque execution: script decodes embedded content "
                    "directly into a shell (base64 -d | bash); the fast "
                    "tier cannot verify code it cannot see"
                ),
                incident_level=_SEV2,
                capability_tag=_sticky_tag(
                    "opaque_execution",
                    {"pattern": MULTISTAGE_DECODE_PIPE_SHELL},
                ),
            )
        if MULTISTAGE_EVAL_SUBSTITUTION in flags:
            return GateDecision(
                allow=False,
                reason=(
                    "G5 opaque execution: script eval()s the output of a "
                    "remote fetch (eval $(curl ...)); the fast tier cannot "
                    "verify code fetched at runtime"
                ),
                incident_level=_SEV2,
                capability_tag=_sticky_tag(
                    "opaque_execution",
                    {"pattern": MULTISTAGE_EVAL_SUBSTITUTION},
                ),
            )
        return None

    # -----------------------------------------------------------------
    # Check 4 -- path scoping (SEV1)
    # -----------------------------------------------------------------

    def _check_path_scoping(
        self, script: SlurmScript, user_config: Mapping[str, Any]
    ) -> GateDecision | None:
        # The gate scans the parsed script for restricted-location hits;
        # the contract owns the verdict (SEV1 deny + sticky tag).
        pattern, where = _first_pattern_hit(
            script, self._sensitive_path_patterns
        ) or (None, None)
        return self._decision_from_contract(
            "hpc_output_path_scoping",
            {"type": "hpc_output_path", "matched_pattern": pattern, "where": where},
        )

    # -----------------------------------------------------------------
    # Check 5 -- network egress (SEV2)
    # -----------------------------------------------------------------

    def _check_network_egress(
        self, script: SlurmScript, user_config: Mapping[str, Any]
    ) -> GateDecision | None:
        allow = self._policy.host_allow_list
        for host in script.network_targets:
            if not _host_allowed(host, allow):
                return GateDecision(
                    allow=False,
                    reason=(
                        f"G5 network egress: outbound connection to "
                        f"non-allow-listed host {host!r}"
                    ),
                    incident_level=_SEV2,
                    capability_tag=_sticky_tag(
                        "network_egress", {"host": host}
                    ),
                )
        return None

    # -----------------------------------------------------------------
    # Check 6 -- credential exfiltration (SEV1)
    # -----------------------------------------------------------------

    def _check_credential_exfiltration(
        self, script: SlurmScript, user_config: Mapping[str, Any]
    ) -> GateDecision | None:
        hit = _first_pattern_hit(script, self._credential_patterns)
        if hit is not None:
            pattern, where = hit
            return GateDecision(
                allow=False,
                reason=(
                    f"G5 credential exfiltration: script {where} reads "
                    f"credential material matching {pattern!r}"
                ),
                incident_level=_SEV1,
                capability_tag=_sticky_tag(
                    "credential_exfiltration", {"pattern": pattern}
                ),
            )
        return None

    # -----------------------------------------------------------------
    # Slow tier 1 -- Q-LLM job-intent extraction
    # -----------------------------------------------------------------

    async def extract_job_intent(
        self,
        payload: Any,
        ctx: GateContext,
        decision: GateDecision,
    ) -> GateDecision:
        """
        G5 slow tier: ask the shared code-intent Q-LLM "what is this SLURM
        job trying to do?" and reconcile it against G1's recorded user
        intent. Mirrors ``G4CodeGate.extract_code_intent`` on the same
        agent and helpers; the difference is the input (a resolved SLURM
        script) and the weapons-relevant dual-use SEV1 path.

        Behavior (pass-through when disabled / no agent / fast-tier
        already denied / empty script):

        - **Low-confidence intent** -> SEV2 default-deny. An incoherent
          Q-LLM read of an HPC job is the obfuscation signal.
        - **High-confidence weapons-relevant dual-use** (CHEM / BIO /
          NUCLEAR at or above ``dual_use_high_confidence_threshold``) ->
          SEV1 deny + sticky.
        - **Intent mismatch** against the user prompt's recorded intent
          (``ctx.capability_registry.get("user:prompt").metadata
          ["intent_summary"]`` + dual-use marker) -> SEV2 deny.
        - **Match** -> allow with the decision annotated for provenance.
        """
        if (
            not self.enabled
            or self._code_intent_extraction_agent is None
            or not decision.allow
        ):
            return decision
        if not isinstance(payload, dict):
            return decision
        slurm_script = payload.get("slurm_script")
        if not isinstance(slurm_script, str) or not slurm_script.strip():
            return decision

        prompt = _JOB_INTENT_PROMPT_TEMPLATE.format(script=slurm_script)
        intent: CodeIntentExtraction = (
            await run_code_intent_extraction_with_self_consistency(
                self._code_intent_extraction_agent,
                prompt,
                samples=self._code_intent_self_consistency_samples,
            )
        )

        # ----- Low-confidence path -> SEV2 default-deny ----------
        if intent.confidence < self._code_intent_confidence_threshold:
            return decision.replace_with(
                allow=False,
                reason=(
                    f"G5 slow-tier: job-intent confidence "
                    f"{intent.confidence:.2f} below threshold "
                    f"{self._code_intent_confidence_threshold:.2f}; "
                    f"default-deny"
                ),
                incident_level=_SEV2,
            )

        # ----- High-confidence weapons-dual-use -> SEV1 sticky ---
        if (
            intent.dual_use_flag in self._weapons_dual_use
            and intent.confidence >= self._dual_use_high_confidence_threshold
        ):
            return decision.replace_with(
                allow=False,
                reason=(
                    f"G5 slow-tier: high-confidence dual-use job "
                    f"(dual_use={intent.dual_use_flag.value}, "
                    f"confidence={intent.confidence:.2f}); SEV1 deny "
                    f"(intent={intent.intent_summary!r})"
                ),
                incident_level=_SEV1,
                capability_tag=_sticky_tag(
                    "dual_use_job",
                    {
                        "dual_use": intent.dual_use_flag.value,
                        "confidence": intent.confidence,
                    },
                ),
            )

        # ----- Intent mismatch vs G1 user prompt -> SEV2 ---------
        g1_summary, g1_dual_use = _read_g1_intent(ctx)
        mismatch, mismatch_reason = _detect_intent_mismatch(
            code_intent=intent,
            g1_intent_summary=g1_summary,
            g1_dual_use=g1_dual_use,
            high_stakes_categories=self._high_stakes_code_categories,
        )
        if mismatch:
            return decision.replace_with(
                allow=False,
                reason=(
                    f"G5 slow-tier: job-intent mismatch: {mismatch_reason} "
                    f"(job_intent={intent.intent_summary!r})"
                ),
                incident_level=_SEV2,
            )

        # ----- Clean intent -> allow, annotated -------------------
        category_text = ",".join(sorted(intent.categories)) or "(none)"
        return decision.replace_with(
            reason=(
                f"{decision.reason}; G5 slow-tier ok "
                f"(categories={category_text}, "
                f"dual_use={intent.dual_use_flag.value}, "
                f"confidence={intent.confidence:.2f})"
            ),
        )

    async def _check_slow_when_enabled(
        self,
        payload: Any,
        ctx: GateContext,
        decision: GateDecision,
    ) -> GateDecision:
        """
        Delegate to ``extract_job_intent`` so callers using the base
        ``Gate.check_slow`` dispatch get the Q-LLM intent check. The
        chained-job DAG walk is invoked separately by the capability via
        ``walk_dependency_dag`` (it needs a request-scoped job resolver
        the gate doesn't own).
        """
        return await self.extract_job_intent(payload, ctx, decision)

    # -----------------------------------------------------------------
    # Slow tier 2 -- chained-job dependency DAG walk
    # -----------------------------------------------------------------

    def walk_dependency_dag(
        self,
        script: SlurmScript,
        user_config: Mapping[str, Any],
        resolve_job: JobScriptResolver,
    ) -> GateDecision | None:
        """
        Walk the SLURM ``--dependency`` graph rooted at ``script`` and
        re-apply the fast-tier checks to every reachable node, returning
        the first deny `GateDecision` (annotated with the offending node)
        or None when the whole chain is clean.

        ``--dependency=afterok:JOBID`` / ``afterany:JOBID`` / ``singleton``
        clauses reference upstream jobs whose resolved scripts come from
        ``resolve_job`` (the job-template repo / a submitted-job registry
        -- no live scheduler needed). This catches the SciAgentBench B2.6
        cross-boundary chain: a benign-looking submission whose dependency
        triggers an unauthorized (e.g. mining) job. Bounded by
        ``dag_max_depth`` / ``dag_max_nodes`` with cycle detection.
        """
        if not self._chained_job_dag_enabled:
            return None

        visited: set[str] = set()
        # Queue of (job_ref, depth). Seed from the root's dependencies.
        queue: list[tuple[str, int]] = [
            (jid, 1)
            for dep in script.directives.dependencies
            for jid in dep.job_ids
        ]
        nodes_examined = 0

        while queue:
            job_ref, depth = queue.pop(0)
            if job_ref in visited:
                continue
            visited.add(job_ref)
            if depth > self._dag_max_depth:
                logger.warning(
                    "VISTAGuard G5: dependency walk hit max depth %d at "
                    "job %r; stopping descent",
                    self._dag_max_depth, job_ref,
                )
                continue
            if nodes_examined >= self._dag_max_nodes:
                logger.warning(
                    "VISTAGuard G5: dependency walk hit max nodes %d; "
                    "stopping (graph too large)",
                    self._dag_max_nodes,
                )
                break

            dep_text = resolve_job(job_ref)
            if dep_text is None:
                # Can't resolve this node's script -- best-effort walk
                # skips it rather than default-denying the whole chain on
                # a missing template.
                logger.info(
                    "VISTAGuard G5: dependency job %r not resolvable; "
                    "skipping in DAG walk",
                    job_ref,
                )
                continue
            nodes_examined += 1

            try:
                dep_script = parse_slurm_script(dep_text)
            except Exception as exc:  # pragma: no cover - parser is no-raise
                return GateDecision(
                    allow=False,
                    reason=(
                        f"G5 chained-job DAG: dependency job {job_ref!r} "
                        f"failed to parse ({type(exc).__name__}: {exc}); "
                        f"default-deny"
                    ),
                    incident_level=_SEV2,
                )

            deny = self._run_fast_checks(dep_script, user_config)
            if deny is not None:
                return deny.replace_with(
                    reason=(
                        f"G5 chained-job DAG: dependency job {job_ref!r} "
                        f"denied: {deny.reason}"
                    ),
                )

            # Descend into this node's own dependencies.
            for dep in dep_script.directives.dependencies:
                for jid in dep.job_ids:
                    if jid not in visited:
                        queue.append((jid, depth + 1))

        return None


# Slow-tier prompt: the input is a *resolved* SLURM job script. 
_JOB_INTENT_PROMPT_TEMPLATE = """\
Tool: submit_hpc_job
This is a resolved SLURM batch job script the agent is about to submit \
to an HPC scheduler. Extract what the job is trying to do.
SLURM script:
---
{script}
---
"""


# -----------------------------------------------------------------
# Module-level helpers
# -----------------------------------------------------------------


def _sticky_tag(check: str, extra: Mapping[str, Any]) -> CapabilityTag:
    """
    Build the capability tag attached to a G5 deny. 
    """
    metadata: dict[str, Any] = {"sticky": True, "g5_check": check}
    metadata.update(extra)
    return CapabilityTag(
        source="tool:submit_hpc_job",
        taint=True,
        metadata=metadata,
    )


def _coalesce_int(primary: int | None, fallback: Any) -> int | None:
    if primary is not None:
        return primary
    return _opt_int(fallback)


def _host_allowed(host: str, allow_list: Sequence[str]) -> bool:
    h = host.strip().lower()
    if not h:
        return True
    for pattern in allow_list:
        if fnmatch(h, pattern.lower()):
            return True
    return False


def _first_pattern_hit(
    script: SlurmScript, patterns: Sequence[str]
) -> tuple[str, str] | None:
    """
    Return ``(pattern, location)`` for the first ``pattern`` found in
    the script, preferring a structured file-path token (more precise)
    over a raw-text match. ``location`` is ``"path"`` or ``"body"`` for
    the deny reason.
    """
    path_blob = "\n".join(script.file_paths)
    for pattern in patterns:
        if pattern in path_blob:
            return pattern, "path"
    for pattern in patterns:
        if pattern in script.raw:
            return pattern, "body"
    return None


# -----------------------------------------------------------------
# Policy file loading
# -----------------------------------------------------------------


def load_allocation_policy(path: Path) -> AllocationPolicy:
    """
    Read the allocation-policy file at ``path``.

    Missing / unreadable / malformed / wrong-version file -> bundled
    defaults with a WARNING (matching ``load_kb_policy``). The settings
    work item wires this to ``<contracts_dir>/g5_allocation_policy.json``
    at sidecar startup; it lives here so the gate and its tests share
    one loader.
    """
    if not path.exists():
        logger.info(
            "VISTAGuard G5: allocation-policy file %s not found; using "
            "bundled defaults (no allocation enforcement)",
            path,
        )
        return AllocationPolicy.default()

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "VISTAGuard G5: cannot read allocation-policy file %s "
            "(%s: %s); using bundled defaults",
            path, type(exc).__name__, exc,
        )
        return AllocationPolicy.default()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            "VISTAGuard G5: allocation-policy file %s is not valid JSON "
            "(%s); using bundled defaults",
            path, exc,
        )
        return AllocationPolicy.default()

    try:
        policy = AllocationPolicy.from_dict(data)
    except ValueError as exc:
        logger.warning(
            "VISTAGuard G5: allocation-policy file %s rejected (%s); "
            "using bundled defaults",
            path, exc,
        )
        return AllocationPolicy.default()

    logger.info(
        "VISTAGuard G5: loaded allocation policy from %s "
        "(%d allocation(s), %d denylisted binaries, %d allow-listed hosts)",
        path,
        len(policy.allocations),
        len(policy.binary_denylist),
        len(policy.host_allow_list),
    )
    return AllocationPolicy(
        version=policy.version,
        allocations=policy.allocations,
        binary_denylist=policy.binary_denylist,
        host_allow_list=policy.host_allow_list,
        loaded_from=path,
    )


__all__ = [
    "ALLOCATION_POLICY_FILENAME",
    "ALLOCATION_POLICY_VERSION",
    "DEFAULT_CREDENTIAL_PATTERNS",
    "DEFAULT_HOST_ALLOW_LIST",
    "DEFAULT_MINING_BINARY_DENYLIST",
    "DEFAULT_MINING_SIGNATURES",
    "DEFAULT_SENSITIVE_PATH_PATTERNS",
    "AllocationLimits",
    "AllocationPolicy",
    "G5HpcJobGate",
    "JobScriptResolver",
    "SlurmScript",
    "load_allocation_policy",
    "parse_slurm_script",
]
