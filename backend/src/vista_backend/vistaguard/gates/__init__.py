"""
VISTAGuard gate implementations.
"""

from .base import Gate, GateContext, GateDecision, PassThroughGate
from .g2_tool import (
    HIGH_STAKES_FALLBACK,
    G2ToolGate,
    ToolDescriptor,
    ToolMetadata,
    discover_high_stakes_tools,
    discover_tool_metadata,
)
from .g3_anomaly import AnomalyResult, detect_embedding_anomalies
from .g3_hybrid import (
    MirroredRetrievalResult,
    inject_hybrid_args,
    merge_retrievals,
    should_inject_hybrid,
)
from .g3_rag import (
    DEFAULT_QUERY_INJECTION_PATTERNS,
    G3RagGate,
    KB_POLICY_FILENAME,
    KB_POLICY_VERSION,
    KbPolicy,
    ParsedChunk,
    SanitizedChunk,
    hash_chunk,
    load_kb_policy,
    parse_rag_search_result,
    tag_rag_chunks,
    tag_sanitized_rag_chunks,
)

__all__ = [
    "AnomalyResult",
    "DEFAULT_QUERY_INJECTION_PATTERNS",
    "G2ToolGate",
    "G3RagGate",
    "Gate",
    "GateContext",
    "GateDecision",
    "HIGH_STAKES_FALLBACK",
    "KB_POLICY_FILENAME",
    "KB_POLICY_VERSION",
    "KbPolicy",
    "MirroredRetrievalResult",
    "ParsedChunk",
    "PassThroughGate",
    "SanitizedChunk",
    "ToolDescriptor",
    "ToolMetadata",
    "detect_embedding_anomalies",
    "discover_high_stakes_tools",
    "discover_tool_metadata",
    "hash_chunk",
    "inject_hybrid_args",
    "load_kb_policy",
    "merge_retrievals",
    "parse_rag_search_result",
    "should_inject_hybrid",
    "tag_rag_chunks",
    "tag_sanitized_rag_chunks",
]