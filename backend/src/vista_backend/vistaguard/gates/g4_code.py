"""
G4 Code Gate -- fast-tier Semgrep scan of LLM-emitted code.

G4 fires on tool calls that ship code into the sandbox -- today
that's ``run_bash`` (bash command body) and ``create_file`` (file
content, language inferred from the path extension). The fast tier
runs Semgrep against the extracted code with the community
``p/security-audit`` ruleset + the VISTAGuard-specific bundle
under ``vistaguard/contracts/semgrep/``. Findings drive the
deny/log decision:

- Any ERROR-severity finding -> SEV2 deny.
- WARNING-severity findings only -> SEV3 incident, allow.
- No findings -> allow.

The slow-tier Q-LLM intent extraction is shipped in the separate
phase-3 issue ``[phase-3] Implement G4 slow-tier``; this module
only ships the fast tier.

## Per-tool overrides

Each tool's MCP descriptor can declare which rule IDs to disable
for code shipped to that tool. The format is a list of rule IDs
restricted to the ``vista-*`` namespace (community rules from
``p/security-audit`` can't be disabled per-tool, only globally).
At construction the gate accepts ``per_tool_overrides`` as a
``Mapping[str, Sequence[str]]``; the entries are validated lazily
when a tool is invoked. The lazy validation is the load-bearing
property of the Bell-LaPadula default-deny posture: an entry that
fails validation denies *that tool's call*, not the entire gate.

## Semgrep invocation

The gate shells out to the ``semgrep`` CLI via ``asyncio.subprocess``
so the gate stays compatible with the optional-dependency story --
deployments that haven't installed ``vista-backend[vistaguard-g4]``
should construct the gate fine but get a deterministic "semgrep
not available" path when ``semgrep_enabled=True``. The ``--json``
output is parsed into ``SemgrepFinding`` dataclasses; the gate
does NOT depend on Semgrep's internal Python API.

Failure modes are explicit:

- Semgrep binary not found -> deny SEV2 (the operator asked for
  G4 but didn't install it).
- Semgrep exits non-zero with no parseable JSON -> deny SEV2.
- Semgrep timeout -> deny SEV2.
- Semgrep produces a parseable response with zero findings ->
  allow.

The defaults match the rest of VISTAGuard: when the security
classifier can't reach a trustworthy verdict, default-deny rather
than fall through to allow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..capabilities import DualUseMarker
from ..quarantine import (
    HIGH_STAKES_CODE_CATEGORIES,
    CodeIntentExtraction,
    run_code_intent_extraction_with_self_consistency,
)
from .base import Gate, GateContext, GateDecision


logger = logging.getLogger(__name__)


# Default low-confidence threshold below which the slow tier
# defaults to deny. Matches G1's intent-extraction default for
# consistency; operators with measured Q-LLM calibration data can
# lower it.
_DEFAULT_CODE_INTENT_CONFIDENCE_THRESHOLD: float = 0.5


# -----------------------------------------------------------------
# Configuration constants
# -----------------------------------------------------------------


DEFAULT_SEMGREP_CONFIG: str = "p/security-audit"
"""
Default community ruleset Semgrep loads in addition to the
VISTAGuard-bundled rules. 
"""


_BUNDLED_RULES_SUBPATH = "contracts/semgrep"


def bundled_rules_dir() -> Path:
    """
    Return the absolute path to the package-bundled VISTAGuard
    Semgrep rules. 
    """
    return (Path(__file__).parent.parent / _BUNDLED_RULES_SUBPATH).resolve()


DEFAULT_SEMGREP_TIMEOUT_SECONDS: float = 30.0
"""
Per-invocation Semgrep timeout. A timed-out run defaults to deny
SEV2 (consistent with the rest of VISTAGuard's fail-closed
posture).
"""


# Language inference for `create_file`.
_LANGUAGE_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".pyw": "python",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".js": "javascript",
    ".ts": "typescript",
    ".rb": "ruby",
    ".go": "go",
    ".rs": "rust",
}


_VALID_SEMGREP_SEVERITIES: frozenset[str] = frozenset(
    {"ERROR", "WARNING", "INFO"}
)


# -----------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------


@dataclass(frozen=True)
class SemgrepFinding:
    """
    A single Semgrep finding, normalized into the fields G4 actually
    consumes. The full Semgrep result dict is preserved in
    ``extra`` for downstream diagnostics; the typed fields are the
    contract gates and tests pin against.
    """

    check_id: str
    severity: str
    message: str
    start_line: int
    end_line: int
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractedCode:
    """
    Result of ``_extract_code(payload)``: the code blob about to be
    scanned plus the language Semgrep should be told to parse it as.
    """

    scan: bool
    code: str = ""
    language: str = ""
    reason: str = ""


# -----------------------------------------------------------------
# G4CodeGate
# -----------------------------------------------------------------


class G4CodeGate(Gate):
    """
    G4 fast-tier code scanner.
    """

    name = "G4"

    def __init__(
        self,
        *,
        enabled: bool = True,
        semgrep_enabled: bool = False,
        semgrep_config: str = DEFAULT_SEMGREP_CONFIG,
        bundled_rules_dir: Path | None = None,
        per_tool_overrides: Mapping[str, Sequence[str]] | None = None,
        semgrep_timeout: float = DEFAULT_SEMGREP_TIMEOUT_SECONDS,
        semgrep_executable: str = "semgrep",
        scan_tools: frozenset[str] = frozenset(
            {"run_bash", "create_file"}
        ),
        code_intent_extraction_agent: Any | None = None,
        code_intent_confidence_threshold: float = _DEFAULT_CODE_INTENT_CONFIDENCE_THRESHOLD,
        code_intent_self_consistency_samples: int = 1,
        high_stakes_code_categories: frozenset[str] | None = None,
    ) -> None:
        super().__init__(enabled=enabled)
        self._semgrep_enabled = semgrep_enabled
        self._semgrep_config = semgrep_config
        self._bundled_rules_dir = (
            bundled_rules_dir
            if bundled_rules_dir is not None
            else globals()["bundled_rules_dir"]()
        )
        self._per_tool_overrides: dict[str, Any] = dict(
            per_tool_overrides or {}
        )
        self._semgrep_timeout = semgrep_timeout
        self._semgrep_executable = semgrep_executable
        self._scan_tools: frozenset[str] = scan_tools

        # Slow-tier configuration. The agent is supplied at
        # construction by the sidecar 
        self._code_intent_extraction_agent: Any | None = (
            code_intent_extraction_agent
        )
        self._code_intent_confidence_threshold = code_intent_confidence_threshold
        self._code_intent_self_consistency_samples = max(
            1, int(code_intent_self_consistency_samples)
        )
        self._high_stakes_code_categories: frozenset[str] = (
            high_stakes_code_categories
            if high_stakes_code_categories is not None
            else HIGH_STAKES_CODE_CATEGORIES
        )

    # -----------------------------------------------------------------
    # Read-only views
    # -----------------------------------------------------------------

    @property
    def semgrep_enabled(self) -> bool:
        return self._semgrep_enabled

    @property
    def semgrep_config(self) -> str:
        return self._semgrep_config

    @property
    def bundled_rules_dir(self) -> Path:
        return self._bundled_rules_dir

    @property
    def semgrep_timeout(self) -> float:
        return self._semgrep_timeout

    @property
    def scan_tools(self) -> frozenset[str]:
        return self._scan_tools

    @property
    def code_intent_extraction_agent(self) -> Any | None:
        return self._code_intent_extraction_agent

    @property
    def code_intent_confidence_threshold(self) -> float:
        return self._code_intent_confidence_threshold

    @property
    def high_stakes_code_categories(self) -> frozenset[str]:
        return self._high_stakes_code_categories

    def attach_code_intent_extraction_agent(self, agent: Any) -> None:
        """
        Store the PydanticAI code-intent Agent for the slow-tier path.
        """
        self._code_intent_extraction_agent = agent

    # -----------------------------------------------------------------
    # Fast-tier check
    # -----------------------------------------------------------------

    async def _check_fast_when_enabled(
        self,
        payload: Any,
        ctx: GateContext,
    ) -> GateDecision:
        """
        Extract code from the payload, run Semgrep, route by
        finding severity.
        """
        if not isinstance(payload, dict):
            raise TypeError(
                f"G4CodeGate expected payload dict with 'tool_name'/'args', "
                f"got {type(payload).__name__}"
            )
        tool_name = payload.get("tool_name")
        args = payload.get("args")
        if not isinstance(tool_name, str) or not isinstance(args, dict):
            raise TypeError(
                "G4CodeGate payload must be "
                "{'tool_name': str, 'args': dict}; got "
                f"tool_name={type(tool_name).__name__}, "
                f"args={type(args).__name__}"
            )

        # Out-of-scope tool -> allow without scanning. G4 is only
        # interested in code-bearing tools.
        if tool_name not in self._scan_tools:
            return GateDecision(
                allow=True,
                reason=f"G4: tool {tool_name!r} not in scan_tools (skip)",
            )

        # Per-tool override (Bell-LaPadula: malformed entry denies).
        try:
            disabled = self._disabled_rules_for_tool(tool_name)
        except ValueError as exc:
            return GateDecision(
                allow=False,
                reason=(
                    f"G4 malformed per-tool override for {tool_name!r}: "
                    f"{exc}"
                ),
                incident_level=2,
            )

        extracted = _extract_code(tool_name, args)
        if not extracted.scan or not extracted.code.strip():
            return GateDecision(
                allow=True,
                reason=(
                    f"G4: nothing to scan on call to {tool_name!r} "
                    f"({extracted.reason or 'empty code'})"
                ),
            )

        # Fast-tier is *only* the Semgrep layer. 
        if not self._semgrep_enabled:
            return GateDecision(
                allow=True,
                reason=(
                    f"G4: semgrep_enabled=False; fast-tier no-op on "
                    f"{tool_name!r}"
                ),
            )

        try:
            findings = await self._run_semgrep(
                extracted.code, extracted.language
            )
        except Exception as exc:  
            logger.warning(
                "VISTAGuard G4: semgrep invocation failed (%s: %s); "
                "default-deny",
                type(exc).__name__, exc,
            )
            return GateDecision(
                allow=False,
                reason=(
                    f"G4: semgrep invocation failed "
                    f"({type(exc).__name__}: {exc}); default-deny"
                ),
                incident_level=2,
            )

        # Filter out disabled rules.
        if disabled:
            findings = [
                f for f in findings if f.check_id not in disabled
            ]

        return self._decide_from_findings(tool_name, findings)

    # -----------------------------------------------------------------
    # Severity routing
    # -----------------------------------------------------------------

    def _decide_from_findings(
        self,
        tool_name: str,
        findings: list[SemgrepFinding],
    ) -> GateDecision:
        """
        Route Semgrep findings into a single ``GateDecision``.

        Any ERROR finding denies SEV2 (the gate names the
        highest-severity rule plus the count of additional rules so
        the structured log lets ops triage without re-running
        Semgrep). WARNING findings allow but record a SEV3 incident
        with the same shape.
        """
        errors = [f for f in findings if f.severity == "ERROR"]
        warnings = [f for f in findings if f.severity == "WARNING"]

        if errors:
            primary = errors[0]
            other_ids = sorted({f.check_id for f in errors[1:]})
            reason_extra = (
                f" (+{len(other_ids)} more: {other_ids[:4]})"
                if other_ids else ""
            )
            return GateDecision(
                allow=False,
                reason=(
                    f"G4 semgrep ERROR on {tool_name!r}: "
                    f"{primary.check_id} at L{primary.start_line}: "
                    f"{primary.message[:120]}{reason_extra}"
                ),
                incident_level=2,
            )
        if warnings:
            primary = warnings[0]
            other_ids = sorted({f.check_id for f in warnings[1:]})
            reason_extra = (
                f" (+{len(other_ids)} more: {other_ids[:4]})"
                if other_ids else ""
            )
            return GateDecision(
                allow=True,
                reason=(
                    f"G4 semgrep WARNING on {tool_name!r}: "
                    f"{primary.check_id} at L{primary.start_line}: "
                    f"{primary.message[:120]}{reason_extra}"
                ),
                incident_level=3,
            )
        return GateDecision(
            allow=True,
            reason=f"G4 semgrep ok on {tool_name!r}: 0 findings",
        )

    # -----------------------------------------------------------------
    # Per-tool override resolution
    # -----------------------------------------------------------------

    def _disabled_rules_for_tool(
        self, tool_name: str
    ) -> frozenset[str] | None:
        """
        Return the set of rule IDs disabled for ``tool_name``, or
        ``None`` if no override exists.
        """
        raw = self._per_tool_overrides.get(tool_name)
        if raw is None:
            return None
        if isinstance(raw, (str, bytes)):
            raise ValueError(
                f"override must be a sequence of rule IDs, "
                f"not {type(raw).__name__}"
            )
        if not isinstance(raw, (list, tuple, set, frozenset)):
            raise ValueError(
                f"override must be a list/set; got {type(raw).__name__}"
            )
        rules: set[str] = set()
        for entry in raw:
            if not isinstance(entry, str):
                raise ValueError(
                    f"override entries must be strings; got "
                    f"{type(entry).__name__}"
                )
            if not entry.startswith("vista-"):
                raise ValueError(
                    f"override rule id {entry!r} must be in the "
                    f"`vista-*` namespace (community rules cannot be "
                    f"disabled per-tool)"
                )
            rules.add(entry)
        return frozenset(rules)

    # -----------------------------------------------------------------
    # Slow-tier: Q-LLM code-intent extraction
    # -----------------------------------------------------------------

    async def extract_code_intent(
        self,
        payload: Any,
        ctx: GateContext,
        decision: GateDecision,
    ) -> GateDecision:
        """
        G4 slow-tier: ask the Q-LLM what the code is trying to do,
        and compare against G1's recorded user-intent.

        Behavior:

        - **Pass-through** when the gate is disabled, the
          ``ctx.quarantine_agent`` is None (operator did not enable
          slow tier), the code-intent agent isn't attached, the
          fast-tier already denied (``decision.allow=False``), or
          the payload has no scannable code.
        - **Low-confidence intent** -> SEV2 default-deny. A Q-LLM
          that cannot reach a coherent intent on emitted code is
          the load-bearing signal for an obfuscated payload.
        - **Dual-use mismatch** -> SEV2. The code's
          ``dual_use_flag`` is non-NONE and the user prompt's G1
          intent did not declare the same marker.
        - **High-stakes-category mismatch** -> SEV2. The code
          performs operations in
          ``high_stakes_code_categories`` (default:
          ``credential_access`` / ``data_exfiltration``) and the
          user prompt's G1 intent does not lexically mention that
          category.
        - **Match** -> allow with the existing decision augmented
          to carry the extracted intent for downstream provenance.

        Why a separate ``extract_code_intent`` (rather than relying
        only on ``_check_slow_when_enabled``): like G1's
        ``extract_intent``, this is the public entrypoint the
        sidecar will call directly when wiring G4 into
        ``process_tool_call``. The base ``Gate.check_slow``
        dispatch (which guards on ``ctx.quarantine_agent``) still
        works because ``_check_slow_when_enabled`` delegates here.
        """
        if (
            not self.enabled
            or self._code_intent_extraction_agent is None
            or not decision.allow
        ):
            return decision

        if not isinstance(payload, dict):
            return decision
        tool_name = payload.get("tool_name")
        args = payload.get("args")
        if not isinstance(tool_name, str) or not isinstance(args, dict):
            return decision

        # Only code-bearing tools get scanned. Other tools fall
        # through with the input decision unchanged.
        if tool_name not in self._scan_tools:
            return decision

        extracted = _extract_code(tool_name, args)
        if not extracted.scan or not extracted.code.strip():
            return decision

        prompt = _CODE_INTENT_PROMPT_TEMPLATE.format(
            tool_name=tool_name,
            language=extracted.language,
            code=extracted.code,
        )

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
                    f"G4 slow-tier: code-intent confidence "
                    f"{intent.confidence:.2f} below threshold "
                    f"{self._code_intent_confidence_threshold:.2f} on "
                    f"{tool_name!r}; default-deny"
                ),
                incident_level=2,
            )

        # ----- Read G1's recorded user intent --------------------
        g1_summary, g1_dual_use = _read_g1_intent(ctx)

        # ----- Mismatch detection -------------------------------
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
                    f"G4 slow-tier: code-intent mismatch on {tool_name!r}: "
                    f"{mismatch_reason} (code_intent="
                    f"{intent.intent_summary!r})"
                ),
                incident_level=2,
            )

        # ----- Clean intent -> allow with annotated reason ------
        category_text = ",".join(sorted(intent.categories)) or "(none)"
        return decision.replace_with(
            reason=(
                f"{decision.reason}; G4 slow-tier ok "
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
        Delegate to ``extract_code_intent`` so callers using the
        base ``Gate.check_slow`` dispatch get the same behavior as
        callers invoking ``extract_code_intent`` directly.
        """
        return await self.extract_code_intent(payload, ctx, decision)

    # -----------------------------------------------------------------
    # Semgrep invocation
    # -----------------------------------------------------------------

    async def _run_semgrep(
        self, code: str, language: str
    ) -> list[SemgrepFinding]:
        """
        Invoke the semgrep CLI on ``code`` and parse the JSON
        output into ``SemgrepFinding`` objects.
        """
        executable = shutil.which(self._semgrep_executable)
        if executable is None:
            raise FileNotFoundError(
                f"semgrep executable {self._semgrep_executable!r} not "
                f"found on PATH; install with "
                f"`pip install vista-backend[vistaguard-g4]`"
            )

        # Write the code to a temp file so Semgrep can pick up the
        # language from the suffix 
        suffix = _suffix_for_language(language)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix, delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(code)
            tmp_path = tmp.name
        try:
            cmd = [
                executable,
                "--quiet",
                "--json",
                "--no-git-ignore",
                "--config", self._semgrep_config,
                "--config", str(self._bundled_rules_dir),
                tmp_path,
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=self._semgrep_timeout
                )
            except asyncio.TimeoutError as exc:
                proc.kill()
                await proc.wait()
                raise TimeoutError(
                    f"semgrep timed out after {self._semgrep_timeout}s"
                ) from exc

            # Semgrep exits non-zero when it has findings, so the
            # return code is not a reliable success signal. The
            # contract is: stdout is parseable JSON.
            try:
                payload = json.loads(stdout_b.decode("utf-8", errors="replace"))
            except json.JSONDecodeError as exc:
                stderr_text = stderr_b.decode("utf-8", errors="replace")[:400]
                raise RuntimeError(
                    f"semgrep produced unparseable output "
                    f"(rc={proc.returncode}): {exc}; stderr={stderr_text!r}"
                ) from exc

            return _parse_semgrep_results(payload)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# -----------------------------------------------------------------
# Module-level helpers
# -----------------------------------------------------------------


# -----------------------------------------------------------------
# Slow-tier prompt template + helpers
# -----------------------------------------------------------------


_CODE_INTENT_PROMPT_TEMPLATE = """\
Tool: {tool_name}
Language: {language}
Code:
---
{code}
---
"""


def _read_g1_intent(
    ctx: GateContext,
) -> tuple[str, DualUseMarker]:
    """
    Read G1's recorded user intent from the capability registry.

    Returns ``(intent_summary, dual_use_flag)`` extracted from the
    ``"user:prompt"`` tag's metadata. When the tag is absent or
    the fields are missing, returns ``("", DualUseMarker.NONE)`` so
    the comparator treats the user-intent side as "no information."

    No information on the user-intent side is *not* automatically a
    mismatch: a deployment that hasn't enabled G1 slow tier still
    needs to allow benign code to run. The comparator's mismatch
    decision is between "code declares high-stakes categories" and
    "user-intent declares them too" -- if both sides are empty,
    that's a match.
    """
    tag = ctx.capability_registry.get("user:prompt")
    if tag is None:
        return "", DualUseMarker.NONE

    metadata = tag.metadata or {}
    summary = str(metadata.get("intent_summary", "") or "")

    raw_flag = metadata.get("intent_dual_use_flag")
    if isinstance(raw_flag, str):
        try:
            dual_use = DualUseMarker(raw_flag.lower())
        except ValueError:
            dual_use = DualUseMarker.NONE
    elif isinstance(raw_flag, DualUseMarker):
        dual_use = raw_flag
    else:
        # Fall back to the tag's own `dual_use` field, which G1's
        # slow tier copies into the tag when it updates.
        dual_use = tag.dual_use

    return summary, dual_use


def _detect_intent_mismatch(
    *,
    code_intent: CodeIntentExtraction,
    g1_intent_summary: str,
    g1_dual_use: DualUseMarker,
    high_stakes_categories: frozenset[str],
) -> tuple[bool, str]:
    """
    Apply the G4 intent-mismatch comparator.

    Returns ``(is_mismatch, reason)``. Two routes to a mismatch:

    1. **Dual-use mismatch.** The code's ``dual_use_flag`` is
       non-NONE and G1's recorded dual-use marker either differs
       or is NONE. Different dual-use markers are mismatches
       regardless of which side is "more restrictive" -- the
       semantic check is "does the user's stated intent match the
       code's apparent intent," not a lattice join.
    2. **High-stakes-category mismatch.** The code claims a
       high-stakes category (``credential_access`` /
       ``data_exfiltration`` by default) and G1's recorded intent
       summary does not lexically mention the category.

    The lexical-mention check is intentionally conservative. We
    use the category name with underscores stripped (e.g.,
    ``"credential access"``) and look for that token in the lower-
    cased user-intent summary. A user prompt like "summarize the
    paper" doesn't mention credentials, so credential-access code
    triggers a mismatch. A prompt like "extract credentials from
    the auth log file and summarize" does mention credentials and
    passes -- the deterministic lexical check accepts the user's
    declared intent at face value, on the assumption that an
    attacker capable of jailbreaking past G1's regex *and* the
    Q-LLM's intent extraction has already lost a layer of defense.
    """
    # ----- Dual-use comparison ----------------------------------
    if code_intent.dual_use_flag is not DualUseMarker.NONE:
        if g1_dual_use is DualUseMarker.NONE:
            return True, (
                f"code dual_use={code_intent.dual_use_flag.value} but "
                f"user intent declared no dual-use marker"
            )
        if code_intent.dual_use_flag is not g1_dual_use:
            return True, (
                f"code dual_use={code_intent.dual_use_flag.value} "
                f"differs from user dual_use={g1_dual_use.value}"
            )

    # ----- High-stakes-category comparison ----------------------
    code_categories = set(code_intent.categories)
    flagged = high_stakes_categories & code_categories
    if not flagged:
        return False, ""

    user_text = g1_intent_summary.lower()
    undeclared = []
    for category in sorted(flagged):
        # Match the category as a phrase (e.g. "credential access").
        # A future enhancement: regex with word boundaries, or an
        # operator-supplied per-category vocabulary.
        token = category.replace("_", " ")
        if token in user_text:
            continue
        undeclared.append(category)

    if undeclared:
        return True, (
            f"code performs high-stakes operation(s) {undeclared} "
            f"not declared in user intent {g1_intent_summary!r}"
        )

    return False, ""


def _extract_code(
    tool_name: str, args: Mapping[str, Any]
) -> ExtractedCode:
    """
    Extract the code blob + language from a code-bearing tool call.
    """
    if tool_name == "run_bash":
        command = args.get("command")
        if not isinstance(command, str):
            return ExtractedCode(
                scan=False,
                reason=f"run_bash args lack a string `command`",
            )
        return ExtractedCode(scan=True, code=command, language="bash")

    if tool_name == "create_file":
        content = args.get("content")
        path = args.get("path")
        if not isinstance(content, str):
            return ExtractedCode(
                scan=False,
                reason=f"create_file args lack a string `content`",
            )
        if not isinstance(path, str):
            return ExtractedCode(
                scan=False,
                reason=f"create_file args lack a string `path`",
            )
        ext = "".join(Path(path).suffixes[-1:]).lower() if "." in path else ""
        language = _LANGUAGE_BY_EXT.get(ext)
        if language is None:
            return ExtractedCode(
                scan=False,
                reason=(
                    f"create_file path {path!r} has unrecognized "
                    f"extension {ext!r}; no Semgrep parser to use"
                ),
            )
        return ExtractedCode(scan=True, code=content, language=language)

    return ExtractedCode(
        scan=False,
        reason=f"tool {tool_name!r} not code-bearing",
    )


def _suffix_for_language(language: str) -> str:
    """
    Return a file suffix Semgrep recognizes for the given language.
    """
    table = {
        "python": ".py",
        "bash": ".sh",
        "javascript": ".js",
        "typescript": ".ts",
        "ruby": ".rb",
        "go": ".go",
        "rust": ".rs",
    }
    return table.get(language, ".txt")


def _parse_semgrep_results(
    payload: Mapping[str, Any]
) -> list[SemgrepFinding]:
    """
    Convert Semgrep's ``--json`` output into ``SemgrepFinding`` list.

    Semgrep's JSON shape is:
    ``{"results": [{"check_id": ..., "start": {"line": int},
    "end": {"line": int}, "extra": {"severity": ..., "message": ...}}, ...]}``.
    """
    raw_results = payload.get("results") or []
    if not isinstance(raw_results, list):
        return []

    out: list[SemgrepFinding] = []
    for entry in raw_results:
        if not isinstance(entry, Mapping):
            continue
        try:
            check_id = str(entry["check_id"])
            extra = entry.get("extra") or {}
            severity = str(extra.get("severity", "INFO")).upper()
            if severity not in _VALID_SEMGREP_SEVERITIES:
                severity = "INFO"
            message = str(extra.get("message", ""))
            start = entry.get("start") or {}
            end = entry.get("end") or {}
            start_line = int(start.get("line", 0))
            end_line = int(end.get("line", start_line))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "VISTAGuard G4: skipping malformed semgrep finding "
                "(%s: %s); entry=%r",
                type(exc).__name__, exc, entry,
            )
            continue
        out.append(
            SemgrepFinding(
                check_id=check_id,
                severity=severity,
                message=message,
                start_line=start_line,
                end_line=end_line,
                extra=dict(extra),
            )
        )
    return out


# -----------------------------------------------------------------
# Module-level public API
# -----------------------------------------------------------------


__all__ = [
    "DEFAULT_SEMGREP_CONFIG",
    "DEFAULT_SEMGREP_TIMEOUT_SECONDS",
    "ExtractedCode",
    "G4CodeGate",
    "SemgrepFinding",
    "bundled_rules_dir",
]
