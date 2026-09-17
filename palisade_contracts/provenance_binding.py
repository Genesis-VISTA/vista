"""
provenance_binding -- verify a citation against the retrieval provenance of
the value it supports.

Closed-world replacement for the open-world ``citation_integrity`` check.
Instead of asking "does this DOI exist in the world?" (which needs a live
Crossref / arXiv lookup on every run), this contract asks the question that
actually matters for a RAG-grounded agent: *did this citation come from a
document we actually retrieved, and does the cited identifier match that
document's trusted, ingestion-time metadata?*

That question is answerable with zero network calls. Every citation the
agent emits must bind to a retrieved source:

- ``resolved_source`` is the trusted metadata (``doi`` / ``arxiv`` /
  ``dataset`` / ``report`` / ``title``) of the document the supporting
  chunk actually came from -- resolved by the egress gate (G6) from G3's
  retrieval provenance. ``None`` (or absent) means *nothing retrieved backs
  this citation*.
- ``cited_id`` is what the agent actually cited.

Verdicts:
- no ``cited_id``                       -> nothing to verify (violation)
- ``resolved_source`` missing / None    -> ungrounded / fabricated (violation)
- ``cited_id`` != the source's id/title -> misattributed (violation)
- they match                            -> pass

The misattribution case is *computed* by comparing the two, not read from a
self-declared flag -- so the contract has real discriminating power: a
correctly-cited claim (``cited_id`` matches ``resolved_source``) passes.

The trust comes from binding to **ingestion-time** document metadata, not
the chunk body text. An attacker who poisons a retrieved chunk to
self-assert a fake DOI still fails, because the forged id won't match the
source document's real metadata.

**Legacy shape.** A pre-provenance-binding citation claim that carries only
a bare identifier (``doi`` / ``arxiv`` / ...) with no ``resolved_source``
is treated as ungrounded -- there is no retrieval context to verify it
against, so it cannot be confirmed.

Operator-supplied contract for ``settings.contracts_dir``.
"""

from __future__ import annotations

import re

from palisade.contracts.base import (
    Contract,
    ContractResult,
    claim_is_untrusted,
    register,
)

# Identifier keys a resolved source (or a legacy claim) may carry, most
# specific first.
_SOURCE_ID_KEYS = ("doi", "arxiv", "dataset", "report")
# Where to look for what the agent cited: the explicit ``cited_id`` first,
# then the legacy bare-identifier keys, then a free-text source name.
_CITED_KEYS = ("cited_id", *_SOURCE_ID_KEYS, "source")

_PREFIX_RE = re.compile(r"^\s*(arxiv:|doi:)", re.IGNORECASE)


def _norm(value: object) -> str:
    """Normalize an identifier for comparison: strip an ``arXiv:``/``doi:``
    prefix and surrounding whitespace, casefold."""
    return _PREFIX_RE.sub("", str(value).strip()).strip().casefold()


def _cited_identifier(claim: dict) -> str | None:
    for key in _CITED_KEYS:
        val = claim.get(key)
        if val:
            return str(val)
    return None


def _source_matches(cited: str, source: dict) -> bool:
    target = _norm(cited)
    for key in (*_SOURCE_ID_KEYS, "title"):
        val = source.get(key)
        if val and _norm(val) == target:
            return True
    return False


def _source_label(source: dict) -> str:
    for key in (*_SOURCE_ID_KEYS, "title"):
        if source.get(key):
            return str(source[key])
    return "<unknown source>"


@register
class ProvenanceBindingContract(Contract):
    """A cited source must bind to the retrieval provenance of its value."""

    name = "provenance_binding"
    domain = "provenance"
    applies_to_claims = ("citation",)
    __contract_version__ = "1"

    def check(self, claim: dict) -> ContractResult:
        cited = _cited_identifier(claim)
        if not cited:
            return self.violated(claim, "citation has no identifier to verify")

        # ``resolved_source`` is the trusted metadata of the retrieved
        # document the cited value came from. Absent or None => nothing
        # retrieved backs this citation.
        source = claim.get("resolved_source")
        if claim_is_untrusted(claim) and source is not None:
            # A poisoned/untrusted claim may not self-assert its own retrieval
            # provenance -- only a trusted gate may populate ``resolved_source``.
            # Treating a self-supplied one as real would let an attacker pass by
            # making ``cited_id`` and ``resolved_source`` agree. So drop it.
            source = None
        if source is None:
            return self.violated(
                claim,
                f"cited {cited!r} maps to no retrieved document "
                "(ungrounded / self-asserted / fabricated citation)",
            )
        if not isinstance(source, dict):
            return self.violated(
                claim, f"cited {cited!r} has malformed retrieval provenance"
            )
        if not _source_matches(cited, source):
            return self.violated(
                claim,
                f"cited {cited!r} does not match the source document "
                f"{_source_label(source)!r} the value came from (misattributed)",
            )
        return self.passed(claim, identifier=cited, source=_source_label(source))
