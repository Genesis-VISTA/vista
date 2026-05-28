"""
G1 Prompt Gate -- fast-tier user-prompt classification.

G1 runs *before* the agent loop sees the user prompt. It is the
cheapest, most deterministic line of defense -- regex matches and
attached-file metadata checks, no LLM, no I/O. The slow-tier
intent-extraction layer is the separate phase-3 issue
`[phase-3] Implement G1 slow-tier`; this module only ships the
fast tier.

## What the fast tier checks (in order)

1. **Payload shape.** Reject programmer-error payload shapes
   (non-dict, missing ``user_prompt``, wrong-typed ``attached_files``).
   This path raises ``TypeError`` rather than denying because the
   sidecar/run_stream caller guarantees the shape; a non-conforming
   payload is a bug.
2. **Attached-file policy.** For every entry in ``attached_files``:

   - Reject if ``mime_type`` is outside ``allowed_mime_types``.
   - Reject if ``size_bytes`` exceeds ``max_file_size_bytes`` (when
     present; missing size is allowed).

   First violation denies SEV3. The MIME policy is the load-bearing
   piece -- a deployment that allow-lists ``application/x-msdownload``
   has bigger problems than VISTAGuard can solve.

3. **Jailbreak / instruction-override regex.** The user prompt is
   scanned against the patterns loaded from the configured
   signatures file (default
   ``vistaguard/contracts/jailbreak_signatures.txt``). Any match
   denies SEV2.

4. **CUI marker detection.** The user prompt is scanned for DOE
   Controlled Unclassified Information markers (``CUI//Basic``,
   ``CUI//SP-PRIVACY`` etc.). A match raises a SEV3 incident and
   tags the prompt's capability with ``sensitivity=CUI`` but does
   NOT deny -- a legitimate user supplying CUI data to their own
   assistant is a valid pattern; downstream gates (G3 sensitivity
   tier, G2 high-stakes guard) enforce CUI handling.

5. **PII marker detection.** The user prompt is scanned for SSN and
   credit-card patterns. Detection raises a SEV3 incident and is
   recorded in the capability tag metadata. Like CUI, PII detection
   does NOT deny -- users may legitimately need help with their own
   PII.

## Capability tag on the allow path

When the gate allows, the returned ``GateDecision`` carries a
``CapabilityTag`` whose:

- ``source = "user"``
- ``sensitivity = CUI`` if CUI markers were found, else ``OPEN``
- ``taint = True`` (default-deny: the prompt is untrusted until a
  downstream gate or the slow tier clears it)
- ``provenance_chain = ("user:prompt",)``
- ``metadata`` records ``{"cui_detected": bool, "pii_detected": bool,
  "pii_kinds": list[str], "attached_file_count": int}``

Downstream code that wants to enforce per-sensitivity policy can
read the tag directly; G3's sensitivity-tier check and G2's
high-stakes guard already consult capability tags via the same
registry. Per the integration plan, this is the structural
property that makes G1's classification useful even when the
slow-tier Q-LLM is unavailable -- the deterministic regex layer
still tags the prompt for the rest of the session.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..capabilities import CapabilityTag, DualUseMarker, SensitivityTier
from ..quarantine import (
    IntentExtraction,
    run_intent_extraction_with_self_consistency,
)
from .base import Gate, GateContext, GateDecision


logger = logging.getLogger(__name__)


# Default confidence threshold below which the slow-tier defaults to
# deny. Picked to match the system-prompt instruction to the Q-LLM
# (which tells it that <0.5 means "could not extract"); shipping the
# same number on both sides keeps the contract single-sourced.
_DEFAULT_INTENT_CONFIDENCE_THRESHOLD: float = 0.5


# Default SEV1 confidence threshold for dual-use detection. Below
# this, dual-use detection is still recorded but classified SEV2
# rather than SEV1 -- a low-confidence dual-use hit is more likely
# to be a false positive on benign chemistry/biology prose, and
# SEV1 in the playbook terminates the session.
_DEFAULT_DUAL_USE_SEV1_CONFIDENCE: float = 0.7


# -----------------------------------------------------------------
# Default MIME allow-list
# -----------------------------------------------------------------
#
# Scientific-assistant defaults: text formats, PDFs, common images,
# YAML/JSON/CSV. Deliberately excludes anything executable
# (application/x-msdownload, application/x-sh) and uncommon archive
# formats. Operators with a broader corpus can pass their own
# allow-list at construction time.

DEFAULT_ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/csv",
        "text/x-python",
        "text/tab-separated-values",
        "text/yaml",
        "application/json",
        "application/pdf",
        "application/x-yaml",
        "application/yaml",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
    }
)


# 50 MiB ceiling on individual attachment size. Picked to admit
# typical scientific PDFs without enabling DoS-by-attachment.
DEFAULT_MAX_FILE_SIZE_BYTES: int = 50 * 1024 * 1024


# -----------------------------------------------------------------
# Jailbreak signature loader
# -----------------------------------------------------------------


JAILBREAK_SIGNATURES_FILENAME = "jailbreak_signatures.txt"
"""
Default filename for the jailbreak-signatures override that
operators drop in ``contracts_dir``. The bundled defaults live
inside the package at ``vistaguard/contracts/<filename>``.
"""


def _bundled_signatures_path() -> Path:
    """
    Return the path to the package-bundled signatures file.
    """
    return Path(__file__).parent.parent / "contracts" / JAILBREAK_SIGNATURES_FILENAME


def load_jailbreak_signatures(
    path: Path | None = None,
) -> tuple[re.Pattern[str], ...]:
    """
    Load jailbreak regex patterns from a signatures file.
    """
    if path is None:
        path = _bundled_signatures_path()
    if not path.exists():
        logger.warning(
            "VISTAGuard G1: jailbreak-signatures file %s not found; "
            "loading empty pattern set (jailbreak detection inactive)",
            path,
        )
        return ()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "VISTAGuard G1: cannot read jailbreak-signatures file %s "
            "(%s: %s); loading empty pattern set",
            path, type(exc).__name__, exc,
        )
        return ()

    compiled: list[re.Pattern[str]] = []
    for lineno, raw_line in enumerate(raw.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            compiled.append(re.compile(line, re.IGNORECASE))
        except re.error as exc:
            logger.warning(
                "VISTAGuard G1: signatures file %s line %d failed to "
                "compile (%s); skipping pattern %r",
                path, lineno, exc, line,
            )
    logger.info(
        "VISTAGuard G1: loaded %d jailbreak signature(s) from %s",
        len(compiled), path,
    )
    return tuple(compiled)


# -----------------------------------------------------------------
# CUI marker patterns -- 32 CFR 2002 designations
# -----------------------------------------------------------------
#
# CUI markings follow the form ``CUI//<category>`` with optional
# decoration. We match the form leniently: a literal ``CUI`` token
# followed by a ``//`` separator and either a known category code or
# any ASCII identifier. The fluent-prose false-positive risk is
# low because ``CUI//`` is a distinct token shape; benign
# scientific prose does not contain double-slash tokens.

CUI_MARKER_PATTERNS: tuple[re.Pattern[str], ...] = (
    # General CUI//Basic and CUI//SP-* markings (NARA CUI Registry).
    re.compile(r"\bCUI//(BASIC|SP-[A-Z]+|FED(CON|ONLY)?)\b", re.IGNORECASE),
    # Standalone "CUI//" prefix matches any operator-defined
    # marking that follows the spec format without a known category.
    re.compile(r"\bCUI//[A-Z0-9_-]+\b", re.IGNORECASE),
)


# -----------------------------------------------------------------
# PII marker patterns
# -----------------------------------------------------------------
#
# Deliberately narrow patterns: dashes/spaces required to reduce
# false-positive rate on numeric scientific content (e.g., 9-digit
# accession numbers). A SSN written without dashes still matches the
# Luhn-style credit-card pattern when 13-19 digits long, so we
# accept some false positives on the credit-card side rather than
# a high false-negative rate on the SSN side.

PII_PATTERNS: dict[str, re.Pattern[str]] = {
    # XXX-XX-XXXX with literal dashes -- the canonical SSN form.
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    # 13-19 digit groups separated by spaces or dashes -- catches
    # the common formatted-credit-card layouts (Visa, Mastercard,
    # Amex). The match doesn't run Luhn validation; the goal is to
    # flag, not to forensically classify, so a near-miss is fine.
    "credit_card": re.compile(
        r"\b(?:\d[ -]?){13,19}\b"
    ),
}


# -----------------------------------------------------------------
# G1PromptGate
# -----------------------------------------------------------------


@dataclass(frozen=True)
class _PiiHit:
    """Internal record of a single PII detection."""
    kind: str
    span: tuple[int, int]


class G1PromptGate(Gate):
    """
    G1 fast-tier: deterministic prompt-side classification.

    Constructed by ``VistaGuardSidecar._build_gates`` when
    ``g1_enabled=True``. The check order (first failure wins for
    deny paths) is:

    1. MIME / file-size policy -> deny SEV3.
    2. Jailbreak regex -> deny SEV2.
    3. CUI marker scan -> allow + tag with sensitivity=CUI, SEV3 incident.
    4. PII marker scan -> allow + record in tag metadata, SEV3 incident.

    On the allow path, the returned ``GateDecision.capability_tag``
    classifies the prompt for downstream gates. The tag is also
    written to ``ctx.capability_registry`` under both the prompt
    text content and a stable ``user:prompt`` key so a downstream
    G2 taint walk over tool arguments finds the tag when the
    agent passes prompt-derived strings into a tool call.
    """

    name = "G1"

    def __init__(
        self,
        *,
        enabled: bool = True,
        jailbreak_patterns: tuple[re.Pattern[str], ...] | None = None,
        cui_patterns: tuple[re.Pattern[str], ...] | None = None,
        pii_patterns: Mapping[str, re.Pattern[str]] | None = None,
        allowed_mime_types: frozenset[str] | None = None,
        max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
        intent_extraction_agent: Any | None = None,
        intent_confidence_threshold: float = _DEFAULT_INTENT_CONFIDENCE_THRESHOLD,
        intent_dual_use_sev1_confidence: float = _DEFAULT_DUAL_USE_SEV1_CONFIDENCE,
        intent_self_consistency_samples: int = 1,
    ) -> None:
        super().__init__(enabled=enabled)
        self._jailbreak_patterns: tuple[re.Pattern[str], ...] = (
            jailbreak_patterns
            if jailbreak_patterns is not None
            else load_jailbreak_signatures()
        )
        self._cui_patterns: tuple[re.Pattern[str], ...] = (
            cui_patterns if cui_patterns is not None else CUI_MARKER_PATTERNS
        )
        self._pii_patterns: dict[str, re.Pattern[str]] = dict(
            pii_patterns if pii_patterns is not None else PII_PATTERNS
        )
        self._allowed_mime_types: frozenset[str] = (
            allowed_mime_types
            if allowed_mime_types is not None
            else DEFAULT_ALLOWED_MIME_TYPES
        )
        self._max_file_size_bytes = max_file_size_bytes

        # Slow-tier configuration.
        self._intent_extraction_agent: Any | None = intent_extraction_agent
        self._intent_confidence_threshold = intent_confidence_threshold
        self._intent_dual_use_sev1_confidence = intent_dual_use_sev1_confidence
        self._intent_self_consistency_samples = max(
            1, int(intent_self_consistency_samples)
        )

        if not self._jailbreak_patterns:
            logger.warning(
                "VISTAGuard G1: jailbreak detection is enabled but the "
                "pattern set is empty; jailbreak scan will not deny any "
                "prompts"
            )

    # -----------------------------------------------------------------
    # Slow-tier wiring helpers
    # -----------------------------------------------------------------

    def attach_intent_extraction_agent(self, agent: Any) -> None:
        """
        Store the PydanticAI intent-extraction Agent for the
        slow-tier path. 
        """
        self._intent_extraction_agent = agent

    @property
    def intent_extraction_agent(self) -> Any | None:
        return self._intent_extraction_agent

    @property
    def intent_confidence_threshold(self) -> float:
        return self._intent_confidence_threshold

    @property
    def intent_dual_use_sev1_confidence(self) -> float:
        return self._intent_dual_use_sev1_confidence

    # -----------------------------------------------------------------
    # Read-only views
    # -----------------------------------------------------------------

    @property
    def jailbreak_patterns(self) -> tuple[re.Pattern[str], ...]:
        return self._jailbreak_patterns

    @property
    def cui_patterns(self) -> tuple[re.Pattern[str], ...]:
        return self._cui_patterns

    @property
    def pii_patterns(self) -> dict[str, re.Pattern[str]]:
        return dict(self._pii_patterns)

    @property
    def allowed_mime_types(self) -> frozenset[str]:
        return self._allowed_mime_types

    @property
    def max_file_size_bytes(self) -> int:
        return self._max_file_size_bytes

    # -----------------------------------------------------------------
    # Fast-tier check
    # -----------------------------------------------------------------

    async def _check_fast_when_enabled(
        self,
        payload: Any,
        ctx: GateContext,
    ) -> GateDecision:
        """
        Run MIME -> jailbreak -> CUI -> PII checks in order.
        """
        if not isinstance(payload, dict):
            raise TypeError(
                f"G1PromptGate expected payload dict with 'user_prompt' "
                f"and optional 'attached_files', got "
                f"{type(payload).__name__}"
            )
        user_prompt = payload.get("user_prompt")
        if not isinstance(user_prompt, str):
            raise TypeError(
                "G1PromptGate payload must contain 'user_prompt': str; "
                f"got {type(user_prompt).__name__}"
            )
        attached_files = payload.get("attached_files", [])
        if not isinstance(attached_files, Sequence) or isinstance(
            attached_files, (str, bytes)
        ):
            raise TypeError(
                "G1PromptGate payload 'attached_files' must be a sequence "
                f"of dicts; got {type(attached_files).__name__}"
            )

        # ----- 1. Attached-file policy --------------------------------
        file_deny = self._check_attached_files(attached_files)
        if file_deny is not None:
            return file_deny

        # ----- 2. Jailbreak regex -------------------------------------
        matched = self._match_jailbreak(user_prompt)
        if matched is not None:
            return GateDecision(
                allow=False,
                reason=(
                    f"G1 jailbreak: prompt matches pattern "
                    f"{matched.pattern!r}"
                ),
                incident_level=2,
            )

        # ----- 3. CUI marker scan -------------------------------------
        cui_match = self._match_cui(user_prompt)
        cui_detected = cui_match is not None

        # ----- 4. PII marker scan -------------------------------------
        pii_hits = self._scan_pii(user_prompt)
        pii_kinds = sorted({hit.kind for hit in pii_hits})

        # Build the capability tag for the allowed prompt.
        sensitivity = (
            SensitivityTier.CUI if cui_detected else SensitivityTier.OPEN
        )
        metadata: dict[str, Any] = {
            "cui_detected": cui_detected,
            "pii_detected": bool(pii_hits),
            "pii_kinds": pii_kinds,
            "attached_file_count": len(attached_files),
        }
        if cui_detected and cui_match is not None:
            metadata["cui_marker"] = cui_match.group(0)
        tag = CapabilityTag(
            source="user",
            sensitivity=sensitivity,
            taint=True,
            provenance_chain=("user:prompt",),
            metadata=metadata,
        )
        # Register the tag in the session registry so downstream
        # gates (G2 taint walk) find it when prompt-derived strings
        # flow into tool arguments.
        try:
            ctx.capability_registry.tag("user:prompt", tag)
            if user_prompt:
                ctx.capability_registry.tag(user_prompt, tag)
        except Exception as exc:  # noqa: BLE001 -- defensive on bad registry
            logger.warning(
                "VISTAGuard G1: registry tag write failed (%s: %s); "
                "downstream taint-propagation may be incomplete",
                type(exc).__name__, exc,
            )

        # Choose the incident level for the allow path. CUI is more
        # surprising than PII in a default-OPEN session, but both
        # are SEV3 (informational) per the AC. We surface SEV3 when
        # either signal fires so the sidecar/incident manager can
        # log it; a clean prompt carries None.
        incident_level: int | None = (
            3 if (cui_detected or pii_hits) else None
        )

        reason_parts = ["G1 fast-tier ok"]
        if cui_detected:
            reason_parts.append("cui_detected=True")
        if pii_hits:
            reason_parts.append(f"pii_kinds={pii_kinds}")
        if attached_files:
            reason_parts.append(f"attached_files={len(attached_files)}")
        return GateDecision(
            allow=True,
            reason="; ".join(reason_parts),
            capability_tag=tag,
            incident_level=incident_level,
        )

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def _check_attached_files(
        self, attached_files: Sequence[Any]
    ) -> GateDecision | None:
        """
        Walk ``attached_files`` and deny on the first policy violation.

        Returns ``None`` when every file is permitted (or no files
        attached).
        """
        for idx, entry in enumerate(attached_files):
            if not isinstance(entry, Mapping):
                # Programmer-error path: the sidecar should pass a
                # list of dict-shaped descriptors.
                raise TypeError(
                    f"G1PromptGate attached_files[{idx}] must be a "
                    f"mapping; got {type(entry).__name__}"
                )
            mime_type = entry.get("mime_type")
            name = entry.get("name") or f"<file[{idx}]>"
            if not isinstance(mime_type, str):
                return GateDecision(
                    allow=False,
                    reason=(
                        f"G1 file policy: attached file {name!r} has no "
                        f"mime_type (got {type(mime_type).__name__})"
                    ),
                    incident_level=3,
                )
            if mime_type not in self._allowed_mime_types:
                return GateDecision(
                    allow=False,
                    reason=(
                        f"G1 file policy: attached file {name!r} has "
                        f"mime_type {mime_type!r} not in allow-list "
                        f"(allowed={sorted(self._allowed_mime_types)[:6]}...)"
                    ),
                    incident_level=3,
                )
            size_bytes = entry.get("size_bytes")
            if size_bytes is not None:
                if not isinstance(size_bytes, int):
                    return GateDecision(
                        allow=False,
                        reason=(
                            f"G1 file policy: attached file {name!r} has "
                            f"non-integer size_bytes "
                            f"({type(size_bytes).__name__})"
                        ),
                        incident_level=3,
                    )
                if size_bytes > self._max_file_size_bytes:
                    return GateDecision(
                        allow=False,
                        reason=(
                            f"G1 file policy: attached file {name!r} is "
                            f"{size_bytes} bytes (> {self._max_file_size_bytes} "
                            f"ceiling)"
                        ),
                        incident_level=3,
                    )
        return None

    def _match_jailbreak(self, text: str) -> re.Pattern[str] | None:
        for pattern in self._jailbreak_patterns:
            if pattern.search(text):
                return pattern
        return None

    def _match_cui(self, text: str) -> re.Match[str] | None:
        for pattern in self._cui_patterns:
            m = pattern.search(text)
            if m is not None:
                return m
        return None

    def _scan_pii(self, text: str) -> list[_PiiHit]:
        hits: list[_PiiHit] = []
        for kind, pattern in self._pii_patterns.items():
            for m in pattern.finditer(text):
                hits.append(_PiiHit(kind=kind, span=m.span()))
        return hits

    # -----------------------------------------------------------------
    # Slow-tier: Q-LLM intent extraction
    # -----------------------------------------------------------------

    async def extract_intent(
        self,
        payload: Any,
        ctx: GateContext,
        decision: GateDecision,
    ) -> GateDecision:
        """
        G1 slow-tier: ask the Q-LLM what the user is trying to do.

        Called by the sidecar (or directly by tests) after
        ``check_fast`` returns an allow decision. The slow tier
        does three things:

        1. Run the intent-extraction Q-LLM on the user prompt
           (with optional self-consistency).
        2. Translate the resulting `IntentExtraction` into a
           refined `GateDecision`:

           - **Low confidence** (``confidence <
             intent_confidence_threshold``): SEV2 default-deny.
             A Q-LLM that cannot reach a coherent intent is the
             load-bearing signal for adversarial prompts (Greshake
             et al., Phase 6 evaluation §4).
           - **Dual-use detected, high confidence**
             (``dual_use_flag != NONE`` and ``confidence >=
             intent_dual_use_sev1_confidence``): SEV1. The
             playbook (main proposal §2.3) terminates the session
             on SEV1.
           - **Dual-use detected, moderate confidence**: SEV2.
             Lower confidence collapses to the SEV2 path so a
             false-positive dual-use hit doesn't terminate a
             benign chemistry/biology session.
           - **Clean intent**: allow with the existing tag
             refreshed to include the intent_summary and dual-use
             marker in metadata.

        3. Update ``ctx.capability_registry`` so the user prompt's
           tag carries the extracted intent for downstream
           provenance. Both the ``"user:prompt"`` and content-keyed
           registry entries are refreshed.

        """
        if (
            not self.enabled
            or self._intent_extraction_agent is None
            or not decision.allow
        ):
            return decision

        if not isinstance(payload, dict):
            return decision
        user_prompt = payload.get("user_prompt")
        if not isinstance(user_prompt, str):
            return decision

        intent: IntentExtraction = (
            await run_intent_extraction_with_self_consistency(
                self._intent_extraction_agent,
                user_prompt,
                samples=self._intent_self_consistency_samples,
            )
        )

        # ----- Low-confidence path -> SEV2 default-deny -----------
        if intent.confidence < self._intent_confidence_threshold:
            return decision.replace_with(
                allow=False,
                reason=(
                    f"G1 slow-tier: intent extraction confidence "
                    f"{intent.confidence:.2f} below threshold "
                    f"{self._intent_confidence_threshold:.2f} "
                    f"(intent={intent.intent_summary!r}); default-deny"
                ),
                incident_level=2,
            )

        # ----- Dual-use detection -> bump incident level ----------
        if intent.dual_use_flag is not DualUseMarker.NONE:
            if intent.confidence >= self._intent_dual_use_sev1_confidence:
                # High-confidence dual-use -> SEV1.
                level = 1
            else:
                # Moderate-confidence dual-use -> SEV2.
                level = 2
            updated_tag = self._refresh_prompt_tag(
                ctx=ctx,
                user_prompt=user_prompt,
                base_tag=decision.capability_tag,
                intent=intent,
            )
            return decision.replace_with(
                allow=False,
                reason=(
                    f"G1 slow-tier: dual-use intent detected "
                    f"(flag={intent.dual_use_flag.value}, "
                    f"confidence={intent.confidence:.2f}, "
                    f"intent={intent.intent_summary!r})"
                ),
                capability_tag=updated_tag,
                incident_level=level,
            )

        # ----- Clean intent -> allow with refreshed tag -----------
        updated_tag = self._refresh_prompt_tag(
            ctx=ctx,
            user_prompt=user_prompt,
            base_tag=decision.capability_tag,
            intent=intent,
        )
        return decision.replace_with(
            reason=(
                f"{decision.reason}; "
                f"G1 slow-tier ok (intent={intent.intent_summary!r}, "
                f"confidence={intent.confidence:.2f})"
            ),
            capability_tag=updated_tag,
        )

    async def _check_slow_when_enabled(
        self,
        payload: Any,
        ctx: GateContext,
        decision: GateDecision,
    ) -> GateDecision:
        
        return await self.extract_intent(payload, ctx, decision)

    def _refresh_prompt_tag(
        self,
        *,
        ctx: GateContext,
        user_prompt: str,
        base_tag: CapabilityTag | None,
        intent: IntentExtraction,
    ) -> CapabilityTag:
        """
        Compose a new ``CapabilityTag`` for the user prompt that
        carries the extracted intent in metadata and updates the
        ``dual_use`` field. 
        """
        if base_tag is None:
            base_tag = CapabilityTag(
                source="user",
                sensitivity=SensitivityTier.OPEN,
                taint=True,
                provenance_chain=("user:prompt",),
                metadata={},
            )

        merged_metadata = dict(base_tag.metadata)
        merged_metadata.update(
            {
                "intent_summary": intent.intent_summary,
                "intent_confidence": intent.confidence,
                "intent_dual_use_flag": intent.dual_use_flag.value,
                "intent_reasoning": intent.reasoning,
            }
        )
        # `propagate`-style provenance: append the slow-tier step.
        new_provenance = tuple(
            list(base_tag.provenance_chain)
            + [f"g1_slow:intent={intent.dual_use_flag.value}"]
        )
        new_tag = base_tag.with_changes(
            dual_use=intent.dual_use_flag,
            provenance_chain=new_provenance,
            metadata=merged_metadata,
        )

        try:
            ctx.capability_registry.tag("user:prompt", new_tag)
            if user_prompt:
                ctx.capability_registry.tag(user_prompt, new_tag)
        except Exception as exc:  # noqa: BLE001 -- defensive
            logger.warning(
                "VISTAGuard G1 slow-tier: registry tag write failed "
                "(%s: %s); downstream taint-propagation may not see "
                "the extracted intent",
                type(exc).__name__, exc,
            )
        return new_tag


# -----------------------------------------------------------------
# Module-level public API
# -----------------------------------------------------------------


__all__ = [
    "CUI_MARKER_PATTERNS",
    "DEFAULT_ALLOWED_MIME_TYPES",
    "DEFAULT_MAX_FILE_SIZE_BYTES",
    "G1PromptGate",
    "JAILBREAK_SIGNATURES_FILENAME",
    "PII_PATTERNS",
    "load_jailbreak_signatures",
]
