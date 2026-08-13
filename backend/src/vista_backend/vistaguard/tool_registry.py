"""
ETDI-style tool-descriptor registry (Bhatt et al. arXiv 2506.01333).

This module is the durable "what does a trusted tool look like?"
record. G2 consults it on every tool call to detect *silent
rug-pulls*: an MCP server that quietly replaces a tool's schema,
description, or name behavior mid-session, e.g., to add a new
parameter that smuggles instructions into an otherwise-benign
tool. The registry stores a SHA-256 hash of each tool's canonical
descriptor at startup; on each call, the live cache is re-hashed
and compared against the startup pin (and against an optional
operator-supplied manifest).

## What we hash

Following the work-item's exact wording ("name + parameter schema
+ description"), the canonical-descriptor form is::

    {
      "name": "<tool name>",
      "description": "<tool description or empty string>",
      "inputSchema": <the JSON Schema dict, sorted keys>,
    }

Annotations (`ToolAnnotations`) are intentionally excluded -- they
drive G2's high-stakes routing but are advisory, not behavior-
defining, and operators may legitimately edit them between
deployments. If a future issue wants to expand the hash payload,
`canonical_descriptor` is the single point to extend; the
manifest format includes the hex digest only, so widening the
payload is a hash-rev migration.

## Hash stability

The hash is computed over `json.dumps(canonical_descriptor(...),
sort_keys=True, separators=(",", ":"))`. `sort_keys=True` makes
the hash invariant to JSON-object key order; `separators=...`
removes whitespace so editors that re-flow JSON files don't
change the hash. The descriptor is a plain dict of JSON-
serializable values (strings, dicts, lists, numbers, bools),
which `json.dumps` handles natively.

## Lifecycle

1. **Sidecar `__init__`** loads the optional manifest from
   `contracts_dir/vistaguard_tool_manifest.json` and constructs
   the registry with `manifest=<pinned hashes>`. Missing or
   malformed manifest is logged at INFO/WARNING and yields an
   empty pinned dict; the registry remains usable.

2. **Sidecar `populate_tool_metadata`** calls `pin_startup` for
   each live tool. The first call per `tool_name` pins the
   startup hash; subsequent calls update the *current*
   descriptor only (used by future `notifications/tools/
   list_changed` handlers and by tests that simulate
   rug-pulls).

3. **Sidecar's G2 `process_tool_call` path** calls `verify` for
   every tool call. A failure routes to a SEV2 deny in G2.

## Verification semantics

`verify(tool_name)` returns a `HashVerification` describing:

- `ok=True` and `mismatch_kind=None` when current == startup and
  (when present) manifest == startup.
- `ok=False` and `mismatch_kind="startup"` when the live cache's
  hash differs from the startup pin (the "rug-pull" case).
- `ok=False` and `mismatch_kind="manifest"` when the startup pin
  differs from an operator-supplied manifest entry (the
  "deployment didn't get the expected tool" case).
- `ok=True` and `mismatch_kind=None` with a benign reason when
  the tool is unregistered. The decision-to-deny lives in G2;
  the registry reports facts.

## Threat model and limits

The registry catches **schema/description drift between sidecar
startup and the gate-check point**. It does *not* catch:

- A malicious MCP server that ships a corrupt descriptor from
  the very first connection -- the operator's manifest is the
  defense for that (pin the expected hash in a separate-channel-
  signed file).
- A server that swaps the *implementation* behind an unchanged
  descriptor (a real rug-pull at the network layer). Defense
  against that is the supply-chain registry described in main
  proposal §4.5 and deliberately out of scope here.
- A descriptor that legitimately drifts between deployments
  (e.g., MCP server version bump that adds an optional
  parameter). The manifest path supports this: the operator
  updates the pinned hash when they intentionally upgrade.

AU-9 tamper-evidence (signed append-only manifest) is deferred
to Phase 5-6.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


# -----------------------------------------------------------------
# Constants
# -----------------------------------------------------------------


MANIFEST_FILENAME = "vistaguard_tool_manifest.json"
"""
Default filename for the operator-supplied tool-descriptor pin
manifest. 
"""


MANIFEST_VERSION = 1
"""
Current manifest schema version. Written into every manifest
file; loaders accept v1 only and log a warning on mismatch. 
"""


# -----------------------------------------------------------------
# Result dataclasses
# -----------------------------------------------------------------


@dataclass(frozen=True)
class HashVerification:
    """
    Outcome of `ToolDescriptorRegistry.verify`.
    """

    ok: bool
    reason: str = ""
    expected_hash: str | None = None
    actual_hash: str | None = None
    mismatch_kind: str | None = None


# -----------------------------------------------------------------
# Hashing
# -----------------------------------------------------------------


def canonical_descriptor(
    name: str,
    description: str | None,
    input_schema: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Build the canonical-descriptor dict used as the hash input.
    """
    return {
        "name": name,
        "description": description or "",
        "inputSchema": dict(input_schema or {}),
    }


def hash_descriptor(descriptor: dict[str, Any]) -> str:
    """
    Return the SHA-256 hex digest of the canonical-descriptor
    dict.

    """
    blob = json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def hash_tool_descriptor(
    name: str,
    description: str | None,
    input_schema: dict[str, Any] | None,
) -> str:

    return hash_descriptor(canonical_descriptor(name, description, input_schema))


# -----------------------------------------------------------------
# Manifest I/O
# -----------------------------------------------------------------


def load_manifest(path: Path) -> dict[str, str]:
    """
    Read a tool-pin manifest from `path` and return the
    `{tool_name: sha256_hex}` map.

    Expected on-disk format::

        {
          "version": 1,
          "generated_at": "<ISO 8601>",   // optional, advisory only
          "tools": {
            "<tool_name>": "<sha256 hex>",
            ...
          }
        }
    """
    if not path.exists():
        logger.info(
            "VISTAGuard ETDI: manifest %s not found; no operator pin in effect",
            path,
        )
        return {}

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "VISTAGuard ETDI: cannot read manifest %s (%s: %s); proceeding without pin",
            path,
            type(exc).__name__,
            exc,
        )
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            "VISTAGuard ETDI: manifest %s is not valid JSON (%s); proceeding without pin",
            path,
            exc,
        )
        return {}

    if not isinstance(data, dict):
        logger.warning(
            "VISTAGuard ETDI: manifest %s top-level must be an object, "
            "got %s; proceeding without pin",
            path,
            type(data).__name__,
        )
        return {}

    version = data.get("version")
    if version != MANIFEST_VERSION:
        logger.warning(
            "VISTAGuard ETDI: manifest %s version %r != supported %r; "
            "proceeding without pin",
            path,
            version,
            MANIFEST_VERSION,
        )
        return {}

    tools = data.get("tools")
    if not isinstance(tools, dict):
        logger.warning(
            "VISTAGuard ETDI: manifest %s missing 'tools' object; "
            "proceeding without pin",
            path,
        )
        return {}

    cleaned: dict[str, str] = {}
    for tool_name, h in tools.items():
        if not isinstance(tool_name, str) or not isinstance(h, str):
            logger.warning(
                "VISTAGuard ETDI: manifest %s has non-string entry %r=%r; skipping",
                path,
                tool_name,
                h,
            )
            continue
        cleaned[tool_name] = h.lower()

    logger.info(
        "VISTAGuard ETDI: loaded %d pinned hash(es) from manifest %s",
        len(cleaned),
        path,
    )
    return cleaned


def write_manifest(
    path: Path,
    pinned: dict[str, str],
    *,
    generated_at: str | None = None,
) -> None:
    """
    Write `pinned` to `path` in the v1 manifest format.
    """
    payload: dict[str, Any] = {
        "version": MANIFEST_VERSION,
        "tools": dict(sorted(pinned.items())),
    }
    if generated_at is not None:
        payload["generated_at"] = generated_at

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


# -----------------------------------------------------------------
# ToolDescriptorRegistry
# -----------------------------------------------------------------


class ToolDescriptorRegistry:
    """
    Per-session ETDI registry: startup pins, current cache,
    optional operator manifest, verification.

    """

    def __init__(
        self,
        *,
        manifest: dict[str, str] | None = None,
    ) -> None:
        self._startup_hashes: dict[str, str] = {}
        self._current_descriptors: dict[str, dict[str, Any]] = {}
        self._manifest_pinned: dict[str, str] = {
            # Normalize
            k: v.lower()
            for k, v in (manifest or {}).items()
        }
        # Tools whose startup pin disagrees with the manifest
        self._manifest_violations: dict[str, str] = {}

    # -----------------------------------------------------------------
    # Read-only views
    # -----------------------------------------------------------------

    @property
    def startup_hashes(self) -> dict[str, str]:
        """A copy of the startup-pin hashes per tool."""
        return dict(self._startup_hashes)

    @property
    def manifest_pinned(self) -> dict[str, str]:
        """A copy of the operator-supplied pinned hashes per tool."""
        return dict(self._manifest_pinned)

    @property
    def manifest_violations(self) -> dict[str, str]:
        """A copy of the manifest-vs-startup mismatches per tool."""
        return dict(self._manifest_violations)

    def has_tool(self, tool_name: str) -> bool:
        """True iff a startup pin exists for `tool_name`."""
        return tool_name in self._startup_hashes

    # -----------------------------------------------------------------
    # Mutation -- pinning and re-cache
    # -----------------------------------------------------------------

    def pin_startup(
        self,
        tool_name: str,
        *,
        description: str | None,
        input_schema: dict[str, Any] | None,
    ) -> str:
        """
        Record the canonical descriptor for `tool_name` and pin
        its startup hash (idempotent on the startup pin).
        """
        descriptor = canonical_descriptor(tool_name, description, input_schema)
        h = hash_descriptor(descriptor)
        self._current_descriptors[tool_name] = descriptor

        if tool_name not in self._startup_hashes:
            self._startup_hashes[tool_name] = h
            # First pin -- check the manifest.
            expected = self._manifest_pinned.get(tool_name)
            if expected is not None and expected != h:
                self._manifest_violations[tool_name] = (
                    f"startup hash {h} != manifest-pinned {expected}"
                )
                logger.warning(
                    "VISTAGuard ETDI: tool %r startup hash %s does not match "
                    "operator-pinned %s",
                    tool_name,
                    h,
                    expected,
                )
        return h

    def update_current(
        self,
        tool_name: str,
        *,
        description: str | None,
        input_schema: dict[str, Any] | None,
    ) -> str:
        """
        Replace the current-cache descriptor for `tool_name`.
        """
        descriptor = canonical_descriptor(tool_name, description, input_schema)
        h = hash_descriptor(descriptor)
        self._current_descriptors[tool_name] = descriptor
        return h

    # -----------------------------------------------------------------
    # Verification
    # -----------------------------------------------------------------

    def verify(self, tool_name: str) -> HashVerification:
        """
        Compare the current cache's hash against the startup pin
        """
        if tool_name not in self._startup_hashes:
            return HashVerification(
                ok=True,
                reason=f"tool {tool_name!r} is not registered in the ETDI registry",
            )

        startup_hash = self._startup_hashes[tool_name]
        manifest_pinned = self._manifest_pinned.get(tool_name)
        if tool_name in self._manifest_violations:
            return HashVerification(
                ok=False,
                reason=(
                    f"G2 ETDI manifest pin: tool {tool_name!r} startup hash "
                    f"{startup_hash} does not match operator-pinned "
                    f"{manifest_pinned}"
                ),
                expected_hash=manifest_pinned,
                actual_hash=startup_hash,
                mismatch_kind="manifest",
            )

        descriptor = self._current_descriptors.get(tool_name)
        if descriptor is None:
            return HashVerification(
                ok=False,
                reason=(
                    f"G2 ETDI: tool {tool_name!r} has a startup pin but no "
                    f"current descriptor; registry state is inconsistent"
                ),
                expected_hash=startup_hash,
                actual_hash=None,
                mismatch_kind="startup",
            )

        try:
            current_hash = hash_descriptor(descriptor)
        except (TypeError, ValueError) as exc:
            return HashVerification(
                ok=False,
                reason=(
                    f"G2 ETDI: cannot hash current descriptor for "
                    f"{tool_name!r} ({type(exc).__name__}: {exc})"
                ),
                expected_hash=startup_hash,
                actual_hash=None,
                mismatch_kind="startup",
            )

        if current_hash != startup_hash:
            return HashVerification(
                ok=False,
                reason=(
                    f"G2 ETDI rug-pull: tool {tool_name!r} descriptor hash "
                    f"{current_hash} differs from startup pin {startup_hash}"
                ),
                expected_hash=startup_hash,
                actual_hash=current_hash,
                mismatch_kind="startup",
            )

        return HashVerification(
            ok=True,
            reason=f"tool {tool_name!r} hash matches startup pin",
            expected_hash=startup_hash,
            actual_hash=current_hash,
        )

    # -----------------------------------------------------------------
    # Convenience
    # -----------------------------------------------------------------

    def snapshot_manifest(self) -> dict[str, str]:
        """
        Return `{tool_name: startup_hash}` for use by
        `write_manifest`.
        """
        return dict(self._startup_hashes)


# -----------------------------------------------------------------
# Module-level public API
# -----------------------------------------------------------------


__all__ = [
    "HashVerification",
    "MANIFEST_FILENAME",
    "MANIFEST_VERSION",
    "ToolDescriptorRegistry",
    "canonical_descriptor",
    "hash_descriptor",
    "hash_tool_descriptor",
    "load_manifest",
    "write_manifest",
]
