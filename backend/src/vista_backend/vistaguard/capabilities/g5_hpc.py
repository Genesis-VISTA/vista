"""
`G5HpcCapability` -- the PydanticAI-hook adapter for the G5 HPC Job Gate
(Phase 4).

G5 is the first gate built natively on the capability pattern: it shipped
its core (``G5HpcJobGate``: SLURM parser + six fast-tier checks) and is
wired into PydanticAI here, with no bespoke ``process_tool_call``
plumbing. All policy stays in the gate; this module is the hook surface.

Three hooks:

- ``before_tool_execute`` for ``submit_hpc_job`` / ``cancel_hpc_job`` --
  the primary entry point. It resolves the SLURM script for the call,
  runs ``gate.check_fast``, raises `SkipToolExecution` on deny (the deny
  reason becomes the tool result the model sees), and on allow attaches a
  ``slurm_script`` capability tag to the registry so the slow-tier
  chained-job DAG walk (separate work item) can see it.

- ``before_tool_execute`` for ``run_bash`` -- the SSH-passthrough case.
  When a ``run_bash`` command is an ``ssh`` invocation targeting an HPC
  login node (a host on the gate's allow-list), the embedded remote
  command is extracted and routed through the same gate. A ``run_bash``
  that isn't HPC-bound passes through untouched (G2 / G4 own generic
  ``run_bash`` inspection).

- ``prepare_tools`` -- when the session's trust tier is RESTRICTED or
  below, ``submit_hpc_job`` is removed from the toolset entirely, so the
  model can't even see it.

The capability does not own a Q-LLM agent: the slow tier (separate work
item) pulls the shared code-intent agent from
``ctx.deps.code_intent_extraction_agent`` (G4 and G5 share it because the
prompt shape -- "what is this code trying to do?" -- is the same). This
module wires only the fast tier and the toolset/SSH plumbing.

## Script resolution

The MCP ``submit_hpc_job`` tool takes a *job name* plus parameters
(``node_count`` / ``duration`` / ``script_args``); the resolved SLURM
text is produced server-side by ``submit_job_mcp.py``. The capability
therefore resolves the script in layers (see ``_resolve_submit_script``):
an inline ``slurm_script`` / ``script`` arg is used verbatim when present;
otherwise a job template is read from ``job_templates_dir`` when one is
configured; otherwise a minimal ``#SBATCH`` header is synthesized from the
call's parameters so the allocation + resource-ceiling checks still apply.
The body-level checks (mining / path / credential / egress) need the
script body, so they are fully effective only for the inline and
template-resolved forms; wiring the live template repo to the backend is
the follow-on integration work item.
"""

from __future__ import annotations

import logging
import shlex
from collections.abc import Mapping, Sequence
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired, SkipToolExecution

from ..gates.base import GateContext, GateDecision
from ..gates.slurm_parser import parse_slurm_script, parse_slurm_time
from .base import VistaGuardCapability
from .registry import CapabilityTag, TrustTier

if TYPE_CHECKING:
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import ToolDefinition

    from ..gates.g5_hpc import G5HpcJobGate, JobScriptResolver


logger = logging.getLogger(__name__)


# Tools whose calls G5 inspects. Anything else passes through untouched.
HPC_JOB_TOOLS: frozenset[str] = frozenset({"submit_hpc_job", "cancel_hpc_job"})
RUN_BASH_TOOL = "run_bash"

# Tools removed from the toolset at RESTRICTED trust tier or below.
TIER_GATED_TOOLS: frozenset[str] = frozenset({"submit_hpc_job"})

# Maps a fast-tier deny's ``g5_check`` (from the decision's sticky
# capability tag) to the conventional sticky-capability key passed to
# ``TrustScorer.mark_sticky_denied``. Only the three documented keys are
# wired (Phase 4); Phase 5's table consumes them. Checks not in this map
# still carry a sticky tag in the registry but don't drive a TrustScorer
# sticky key.
STICKY_KEY_BY_CHECK: dict[str, str] = {
    "resource_ceiling": "g5:above_ceiling_resource",
    "allocation": "g5:unauthorized_allocation",
    "credential_exfiltration": "g5:credential_exfil",
}

# Trust-tier ordering by permission level (NORMAL most permissive).
_TIER_RANK: dict[TrustTier, int] = {
    TrustTier.NORMAL: 3,
    TrustTier.ELEVATED: 2,
    TrustTier.RESTRICTED: 1,
    TrustTier.TERMINATED: 0,
}


def _tier_at_or_below(tier: TrustTier, threshold: TrustTier) -> bool:
    """True iff ``tier`` is as restricted as, or more restricted than,
    ``threshold`` (i.e. has fewer permissions)."""
    return _TIER_RANK.get(tier, 0) <= _TIER_RANK[threshold]


class G5HpcCapability(VistaGuardCapability):
    """
    G5 HPC Job Gate exposed as a PydanticAI capability.

    Registers ``before_tool_execute`` (fast tier: submit/cancel +
    run_bash-SSH), ``wrap_tool_execute`` (slow tier: Q-LLM job-intent +
    chained-job DAG walk) and ``prepare_tools`` (trust-tier toolset
    removal). Every hook no-ops when the capability is disabled
    (`is_enabled()` is False).

    The fast tier runs in ``before_tool_execute`` (cheap, deterministic,
    raises `SkipToolExecution` on deny); the expensive slow tier runs in
    ``wrap_tool_execute``, which the framework invokes only *after*
    ``before_tool_execute`` allows -- so fast gates first and the Q-LLM
    is never paid for on a call the fast tier already rejected. A slow-tier
    deny returns the deny message as the tool result without invoking the
    wrapped handler, so the tool never executes.
    """

    gate_flag = "g5_enabled"

    def __init__(
        self,
        sidecar: Any,
        gate: Any,
        settings: Any,
        *,
        job_templates_dir: Path | None = None,
        job_script_resolver: "JobScriptResolver | None" = None,
        require_submit_approval: bool = True,
    ) -> None:
        super().__init__(sidecar, gate, settings)
        self._job_templates_dir = job_templates_dir
        self._job_script_resolver = job_script_resolver
        self._require_submit_approval = require_submit_approval

    @property
    def hpc_gate(self) -> G5HpcJobGate:
        """The underlying `G5HpcJobGate` (typed view of ``self.gate``)."""
        return self._gate  # type: ignore[return-value]

    def _gate_ctx(self) -> GateContext:
        return GateContext(
            capability_registry=self.sidecar.capability_registry,
            trust_scorer=self.sidecar.trust_scorer,
            contracts=self.sidecar.contracts,
            quarantine_agent=self.sidecar.quarantine_agent,
            provenance=self.sidecar.provenance,
        )

    # -----------------------------------------------------------------
    # before_tool_execute
    # -----------------------------------------------------------------

    async def before_tool_execute(
        self,
        ctx: RunContext,
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Gate HPC job submissions and HPC-bound ``run_bash`` SSH commands.

        Other tools pass through unaffected (the inline ``tools=`` filter
        the decorator form would pin).
        """
        if not self.is_enabled() or not isinstance(args, dict):
            return args
        tool_name = tool_def.name
        if tool_name in HPC_JOB_TOOLS:
            return await self._guard_hpc_job(tool_name, args)
        if tool_name == RUN_BASH_TOOL:
            return await self._guard_run_bash(args)
        return args

    async def _guard_hpc_job(
        self, tool_name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        script = self._resolve_submit_script(tool_name, args)
        user_config = self._submit_user_config(args)
        decision = await self.hpc_gate.check_fast(
            {"slurm_script": script, "user_config": user_config},
            self._gate_ctx(),
        )
        if not decision.allow:
            self._record_incident(decision.incident_level or 2, decision.reason)
            self._mark_sticky_if_applicable(decision)
            raise SkipToolExecution(
                self._deny_message(tool_name, decision.reason)
            )
        # Allow: record any non-fatal incident level and tag the script so
        # downstream chained-job analysis (slow tier) can find it.
        if decision.incident_level is not None:
            self._record_incident(decision.incident_level, decision.reason)
        self._tag_slurm_script(tool_name, script)
        return args

    async def _guard_run_bash(self, args: dict[str, Any]) -> dict[str, Any]:
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return args
        payload = extract_ssh_hpc_payload(
            command, self.hpc_gate.policy.host_allow_list
        )
        if payload is None:
            # Not an HPC-bound SSH command. G5 leaves generic run_bash
            # inspection to G2 / G4.
            return args
        decision = await self.hpc_gate.check_fast(
            {"slurm_script": payload, "user_config": {}},
            self._gate_ctx(),
        )
        if not decision.allow:
            self._record_incident(decision.incident_level or 2, decision.reason)
            self._mark_sticky_if_applicable(decision)
            raise SkipToolExecution(
                self._deny_message(RUN_BASH_TOOL, decision.reason)
            )
        if decision.incident_level is not None:
            self._record_incident(decision.incident_level, decision.reason)
        self._tag_slurm_script(RUN_BASH_TOOL, payload)
        return args

    # -----------------------------------------------------------------
    # prepare_tools -- trust-tier toolset removal
    # -----------------------------------------------------------------

    async def prepare_tools(
        self,
        ctx: RunContext,
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        """
        Remove ``submit_hpc_job`` from the toolset when the session's
        trust tier is RESTRICTED or below, so the model can't even see it.
        """
        if not self.is_enabled():
            return tool_defs
        tier = self.sidecar.trust_scorer.current_tier()
        if not _tier_at_or_below(tier, TrustTier.RESTRICTED):
            return tool_defs
        kept: list[ToolDefinition] = []
        for tool_def in tool_defs:
            if tool_def.name in TIER_GATED_TOOLS:
                self._record_incident(
                    2,
                    f"G5 trust tier {tier.value}: removing {tool_def.name!r} "
                    f"from the toolset (RESTRICTED or below)",
                )
                continue
            kept.append(tool_def)
        return kept

    # -----------------------------------------------------------------
    # wrap_tool_execute -- slow tier (Q-LLM intent + chained-job DAG)
    # -----------------------------------------------------------------

    async def wrap_tool_execute(
        self,
        ctx: RunContext,
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: Any,
        handler: Any,
    ) -> Any:
        """
        Run the slow tier around an HPC job execution, then gate
        ``submit_hpc_job`` behind human approval.

        Reached only after ``before_tool_execute`` allowed (the framework
        skips ``wrap_tool_execute`` when ``before_tool_execute`` raises
        `SkipToolExecution`). For ``submit_hpc_job`` / ``cancel_hpc_job``
        and HPC-bound ``run_bash`` this runs the gate's Q-LLM job-intent
        check (``check_slow``) and the chained-job DAG walk; a deny returns
        the deny message as the tool result *without* running the wrapped
        handler.

        Once the policy tiers pass, ``submit_hpc_job`` is deferred for
        human approval via PydanticAI's native deferred-tool-calls
        mechanism: the fast-tier decision metadata is stashed on the
        sidecar (keyed by ``tool_call_id``) for the approval UI, and
        `ApprovalRequired` is raised. ``VistaGuardApprovalCapability``
        resolves it; on approval the framework re-enters this hook with
        ``ctx.tool_call_approved`` set, and the handler runs. On decline
        the call is denied cleanly (a `ToolDenied` result, not an
        exception) and the sidecar records the sticky-on-decline mark.
        """
        if not self.is_enabled() or not isinstance(args, dict):
            return await handler(args)
        tool_name = tool_def.name
        if tool_name not in HPC_JOB_TOOLS and tool_name != RUN_BASH_TOOL:
            return await handler(args)

        # Approved re-entry: the slow tier + approval already ran on the
        # deferring pass; just execute.
        if getattr(ctx, "tool_call_approved", False):
            return await handler(args)

        script = self._slow_tier_script(tool_name, args)
        if not script or not script.strip():
            return await handler(args)
        user_config = self._slow_tier_user_config(tool_name, args)
        gate_ctx = self._gate_ctx()

        # Slow tier 1: Q-LLM job-intent extraction. The base
        # `Gate.check_slow` no-ops when the Q-LLM (quarantine_agent) is
        # absent, so this is a pass-through when the slow tier is off.
        baseline = GateDecision(allow=True, reason="G5 fast-tier passed")
        decision = await self.hpc_gate.check_slow(
            {"slurm_script": script, "user_config": user_config},
            gate_ctx,
            baseline,
        )

        # Slow tier 2: chained-job dependency DAG walk (deterministic; runs
        # independently of the Q-LLM when enabled).
        if decision.allow and self.hpc_gate.chained_job_dag_enabled:
            dag_decision = self._walk_dag(script, user_config)
            if dag_decision is not None and not dag_decision.allow:
                decision = dag_decision

        if not decision.allow:
            self._record_incident(decision.incident_level or 2, decision.reason)
            return self._deny_message(tool_name, decision.reason)
        if decision.incident_level is not None:
            self._record_incident(decision.incident_level, decision.reason)

        # Human-in-the-loop approval for submit_hpc_job, via the native
        # deferred-tool-calls capability (R6). Stash the fast-tier decision
        # metadata for the approval UI before deferring.
        if self._require_submit_approval and tool_name == "submit_hpc_job":
            self._stash_approval_metadata(call.tool_call_id, script, user_config)
            raise ApprovalRequired()

        return await handler(args)

    def _stash_approval_metadata(
        self, tool_call_id: str, script: str, user_config: Mapping[str, Any]
    ) -> None:
        """
        Record the G5 fast-tier decision metadata for ``tool_call_id`` on
        the sidecar so the approval emitter can surface it to the user.
        """
        store = getattr(self.sidecar, "pending_approval_metadata", None)
        if store is None:
            return
        try:
            store[tool_call_id] = self.hpc_gate.fast_tier_metadata(
                script, user_config
            )
        except Exception as exc:  # noqa: BLE001 -- metadata is best-effort
            logger.warning(
                "VISTAGuard G5: failed to build approval metadata (%s: %s)",
                type(exc).__name__, exc,
            )

    def _walk_dag(
        self, script: str, user_config: Mapping[str, Any]
    ) -> GateDecision | None:
        """Parse the resolved script and walk its dependency DAG with the
        capability's job resolver."""
        try:
            parsed = parse_slurm_script(script)
        except Exception:  # pragma: no cover - parser is no-raise
            return None
        if not parsed.directives.dependencies:
            return None
        return self.hpc_gate.walk_dependency_dag(
            parsed, user_config, self._resolve_dependency_job
        )

    def _resolve_dependency_job(self, job_ref: str) -> str | None:
        """
        Resolve a SLURM dependency reference (JOBID or job name) to its
        script text. Prefers an injected resolver; otherwise reads from the
        job-template dir by name and falls back to a prior submission tagged
        in the capability registry under ``slurm_script:job:<ref>``.
        """
        if self._job_script_resolver is not None:
            return self._job_script_resolver(job_ref)
        text = self._read_job_template(job_ref)
        if text is not None:
            return text
        tag = self.sidecar.capability_registry.get(f"slurm_script:job:{job_ref}")
        if tag is not None:
            script = tag.metadata.get("slurm_script")
            if isinstance(script, str):
                return script
        return None

    def _slow_tier_script(self, tool_name: str, args: Mapping[str, Any]) -> str | None:
        """The script text the slow tier inspects for a tool call."""
        if tool_name in HPC_JOB_TOOLS:
            return self._resolve_submit_script(tool_name, args)
        if tool_name == RUN_BASH_TOOL:
            command = args.get("command")
            if not isinstance(command, str):
                return None
            return extract_ssh_hpc_payload(
                command, self.hpc_gate.policy.host_allow_list
            )
        return None

    def _slow_tier_user_config(
        self, tool_name: str, args: Mapping[str, Any]
    ) -> dict[str, Any]:
        if tool_name in HPC_JOB_TOOLS:
            return self._submit_user_config(args)
        return {}

    # -----------------------------------------------------------------
    # Script resolution
    # -----------------------------------------------------------------

    def _resolve_submit_script(
        self, tool_name: str, args: Mapping[str, Any]
    ) -> str:
        """
        Resolve the SLURM script text for a job-tool call. See the module
        docstring for the resolution layers.
        """
        if tool_name == "cancel_hpc_job":
            # No script to inspect; an empty script is allowed by the gate.
            # The call still routes through the hook for tier-gating and
            # provenance.
            return ""
        # 1. Inline script forms (used by inline-script tools and tests).
        for key in ("slurm_script", "script"):
            val = args.get(key)
            if isinstance(val, str) and val.strip():
                return val
        # 2. Job template read from a configured directory.
        job = args.get("job")
        if isinstance(job, str) and self._job_templates_dir is not None:
            text = self._read_job_template(job)
            if text is not None:
                return text
        # 3. Synthesize a minimal #SBATCH header from the parameters so the
        #    allocation + ceiling checks still run.
        return self._synthesize_header(args)

    def _read_job_template(self, job: str) -> str | None:
        """
        Read the first per-cluster job script under ``<job_templates_dir>/<job>/``
        (``job.odo.slurm``, then the other clusters; ``job.slurm`` is the legacy
        pre-rename Odo name). Returns None when no template is found or the
        job name is unsafe.
        """
        base = self._job_templates_dir
        if base is None:
            return None
        # Reject path-traversal job names; templates live one dir deep.
        if "/" in job or ".." in job or not job:
            logger.warning(
                "VISTAGuard G5: refusing to resolve unsafe job name %r", job
            )
            return None
        for filename in ("job.odo.slurm", "job.slurm", "job.frontier.slurm", "job.perlmutter.slurm"):
            candidate = base / job / filename
            try:
                if candidate.is_file():
                    return candidate.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning(
                    "VISTAGuard G5: cannot read job template %s (%s: %s)",
                    candidate, type(exc).__name__, exc,
                )
        return None

    @staticmethod
    def _synthesize_header(args: Mapping[str, Any]) -> str:
        """
        Build a minimal ``#SBATCH`` header from the call parameters. The
        body is unknown in the job-name form, but the directives let the
        allocation + resource-ceiling checks run.
        """
        lines = ["#!/bin/bash -l"]
        account = args.get("account") or args.get("hpc_account")
        if isinstance(account, str) and account:
            lines.append(f"#SBATCH --account={account}")
        node_count = args.get("node_count")
        if isinstance(node_count, int):
            lines.append(f"#SBATCH --nodes={node_count}")
        duration = args.get("duration")
        if isinstance(duration, str) and duration:
            lines.append(f"#SBATCH --time={duration}")
        partition = args.get("partition")
        if isinstance(partition, str) and partition:
            lines.append(f"#SBATCH --partition={partition}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _submit_user_config(args: Mapping[str, Any]) -> dict[str, Any]:
        """
        Build the ``user_config`` the gate consults for the resource and
        allocation checks, from the call parameters.
        """
        cfg: dict[str, Any] = {}
        node_count = args.get("node_count")
        if isinstance(node_count, int):
            cfg["node_count"] = node_count
        duration = args.get("duration")
        if isinstance(duration, str) and duration:
            seconds = parse_slurm_time(duration)
            if seconds is not None:
                cfg["duration_seconds"] = seconds
        for key in ("account", "hpc_account", "nersc_account"):
            val = args.get(key)
            if isinstance(val, str) and val:
                cfg[key] = val
        return cfg

    # -----------------------------------------------------------------
    # Registry tagging
    # -----------------------------------------------------------------

    def _tag_slurm_script(self, tool_name: str, script: str) -> None:
        """
        Attach a ``slurm_script`` capability tag for an allowed job so the
        slow-tier chained-job DAG walk can later read the resolved script.
        """
        if not script.strip():
            return
        value_id = f"slurm_script:{tool_name}"
        tag = CapabilityTag(
            source=f"tool:{tool_name}",
            taint=True,
            metadata={"kind": "slurm_script", "slurm_script": script},
        )
        try:
            self.sidecar.capability_registry.tag(value_id, tag)
        except Exception as exc:  # noqa: BLE001 -- defensive on bad registry
            logger.warning(
                "VISTAGuard G5: registry tag write failed (%s: %s)",
                type(exc).__name__, exc,
            )

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def _record_incident(self, level: int, reason: str) -> None:
        self.sidecar.incident_manager.record(
            level=level, gate="G5", reason=reason
        )

    def _mark_sticky_if_applicable(self, decision: GateDecision) -> None:
        """
        On a fast-tier deny, mark the capability sticky-denied via the
        trust scorer so the same session can't re-request it once the
        score recovers (the probe-then-strike mitigation). Maps the deny's
        ``g5_check`` to a documented sticky key; checks without a mapped key
        are skipped. 
        """
        tag = decision.capability_tag
        if tag is None:
            return
        key = STICKY_KEY_BY_CHECK.get(tag.metadata.get("g5_check", ""))
        if key is not None:
            self.sidecar.trust_scorer.mark_sticky_denied(key)

    @staticmethod
    def _deny_message(tool_name: str, reason: str) -> str:
        return f"VISTAGuard G5 denied call to {tool_name!r}: {reason}"


# -----------------------------------------------------------------
# SSH-passthrough extraction
# -----------------------------------------------------------------


def extract_ssh_hpc_payload(
    command: str, hpc_host_patterns: Sequence[str]
) -> str | None:
    """
    If ``command`` is an ``ssh`` invocation targeting an HPC login node
    (a host matching one of ``hpc_host_patterns``), return the embedded
    remote command string so the gate can parse it as a SLURM script.
    Returns None when the command isn't an HPC-bound SSH passthrough.

    ``ssh user@odo.olcf.ornl.gov 'sbatch job.slurm'`` ->
    ``"sbatch job.slurm"``. Loopback targets never count as HPC hosts
    (so ``ssh localhost ...`` is left to G2 / G4).
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return None
    head = tokens[0].rsplit("/", 1)[-1]
    if head != "ssh":
        return None

    # Walk options to find the host token. ssh options that take a value
    # are skipped together with their value.
    value_opts = {"-p", "-i", "-o", "-l", "-F", "-c", "-b", "-D", "-L", "-R", "-w"}
    idx = 1
    host: str | None = None
    while idx < len(tokens):
        tok = tokens[idx]
        if tok in value_opts:
            idx += 2
            continue
        if tok.startswith("-"):
            idx += 1
            continue
        host = tok
        break
    if host is None:
        return None

    hostname = host.rsplit("@", 1)[-1]
    if hostname in ("localhost", "127.0.0.1", "::1"):
        return None
    if not _matches_hpc_host(hostname, hpc_host_patterns):
        return None

    remote = tokens[idx + 1 :]
    if not remote:
        return None
    return " ".join(remote)


def _matches_hpc_host(hostname: str, patterns: Sequence[str]) -> bool:
    h = hostname.strip().lower()
    if not h:
        return False
    for pattern in patterns:
        p = pattern.lower()
        # Loopback allow-list entries are not HPC login nodes.
        if p in ("localhost", "127.0.0.1", "::1"):
            continue
        if fnmatch(h, p):
            return True
    return False


__all__ = ["G5HpcCapability", "extract_ssh_hpc_payload"]
