"""
Text RAG system with citation metadata extraction.

Reads PDFs, extracts text chunks for semantic search, and also extracts
structured citation metadata (title, authors, DOI, etc.) from the first
few pages of each paper using an LLM. Both the chunks and the
per-document citation records are stored in ChromaDB.

The LLM provider is auto-detected from environment variables. The
resolution mirrors the chat route in ui/app/api/chat/route.ts so the
indexer always uses the same model the rest of the app uses:

    1. Azure OpenAI is used if all three of these are set:
         AZURE_OPENAI_ENDPOINT
         AZURE_OPENAI_API_KEY
         AZURE_OPENAI_DEPLOYMENT_NAME
       (AZURE_OPENAI_API_VERSION defaults to 2025-01-01-preview.)

    2. Otherwise a generic OpenAI-compatible client is used:
         OPENAI_API_KEY     (required)
         OPENAI_BASE_URL    (default: https://api.openai.com/v1)
         OPENAI_MODEL       (default: gpt-4o-mini)
         OPENAI_AUTH_MODE   (default: bearer; set to "api_key" for
                             Azure-style header)
         OPENAI_CHAT_URL    (optional override of the request URL)

Legacy variables ENDPOINT_URL and DEPLOYMENT_NAME are still honored as
fallbacks but log a deprecation warning the first time they're used.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any, Optional
import csv
import io
import json
import logging
import os
import time

import fitz  # PyMuPDF
import chromadb
from chromadb.config import Settings as ChromaSettings
from sentence_transformers import SentenceTransformer
from openai import AzureOpenAI, BadRequestError, OpenAI

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# Configure logging so build_rag's INFO output reaches the terminal even
# when imported by a host (uvicorn / FastAPI backend) that has already
# attached handlers to the root logger.
#
# `logging.basicConfig` is a no-op once any root handler exists. That
# would normally leave us at the mercy of the host's logging config —
# which in the FastAPI backend's case is INFO and *should* propagate,
# but in practice some uvicorn setups install filters or non-stderr
# handlers that swallow our LLM-call logs. So we do two things:
#
#  1. Set our module logger's level explicitly so it can't be silenced
#     by inheriting WARNING from a misconfigured parent.
#  2. If the root logger has no handlers (i.e. we're running standalone,
#     e.g. via `python build_rag.py`), set up a basic stderr handler
#     ourselves. If the root already has handlers, leave them be —
#     propagation will carry our messages up.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
# When the host has already configured handlers on the root logger,
# propagation does the work — don't double-print. When it hasn't,
# basicConfig above already installed one. Either way, an explicit
# stderr handler attached *here* would risk duplication, so we don't.

# ---------------------------------------------------------------------------
# Citation extraction prompt
# ---------------------------------------------------------------------------
CITATION_PROMPT = """\
You are a metadata extraction assistant for scientific publications.

Given the text extracted from a scientific paper, extract the following citation
metadata and return it as a single JSON object (no markdown fencing, no extra text):

{{
  "title": "...",
  "authors": ["First Last", ...],
  "abstract": "...",
  "journal": "...",
  "volume": "...",
  "issue": "...",
  "pages": "...",
  "year": "...",
  "doi": "...",
  "keywords": ["...", ...],
  "publisher": "..."
}}

Rules:
- Use null for any field you cannot determine from the extracted text.
- For `title` and `authors` specifically: if the front-matter text below
  doesn't clearly contain them, the filename hint at the top often
  encodes them (e.g. "JournalAbbrev_-_YEAR_-_LastName_-_Title_Words.pdf").
  Parse them from there if possible rather than returning null.
- Authors should be a list of strings in "First Last" format. If only
  last names are available (common in filename hints), use just the
  last name as a single-token string.
- Keywords should be a list; if none found return an empty list.
- Return ONLY the JSON object, nothing else.

--- FILENAME HINT ---
{filename}
--- END FILENAME HINT ---

--- BEGIN EXTRACTED TEXT ---
{text}
--- END EXTRACTED TEXT ---
"""

# Fields we expect back from the LLM
CITATION_FIELDS = [
    "title", "authors", "abstract", "journal", "volume",
    "issue", "pages", "year", "doi", "keywords", "publisher",
]


# ---------------------------------------------------------------------------
# Retrieval instructions
# ---------------------------------------------------------------------------
# The embedding model is instruction-tuned. Its model card's FAQ: "Do I need
# to add instructions to the query? Yes, this is how the model is trained,
# otherwise you will see a performance degradation." Its
# `config_sentence_transformers.json` leaves `default_prompt_name` null, so
# sentence-transformers prepends nothing unless a caller passes `prompt`.
#
# The same FAQ: "there is no need to add instructions to the document side."
# So `embed_text` and `embed_text_batched` encode chunks bare, and only the
# `query*` methods pass a prompt. That asymmetry is what makes this safe to
# add to an existing deployment: no stored vector changes, so no reindex.
#
# QUERY_INSTRUCTION is kept byte-identical to
# `vista_mcp_server.config.Settings.rag_query_instruction`, which is the
# *query* encoder the MCP server actually serves `rag_search` from.
# `test_embedding_model.py` fails if the two drift. It names no subject
# matter: Knowledge Bases are user-built and may hold any corpus.
QUERY_INSTRUCTION = (
    "Given a search query, retrieve relevant passages from documents"
)

# The citation collection holds "Title. Authors. Journal (Year). DOI"
# strings, not prose, so searching it is a different task than searching
# chunks and gets its own instruction.
CITATION_QUERY_INSTRUCTION = (
    "Given a description of a document, retrieve its bibliographic record"
)


def query_prompt(instruction: str) -> str:
    """Wrap a task description in the format the encoder was trained on."""
    return f"Instruct: {instruction.strip()}\nQuery: "


# ---------------------------------------------------------------------------
# Embedding model
# ---------------------------------------------------------------------------
# Ungated (MIT) and 640-dimension. Kept byte-identical to
# `vista_mcp_server.config.Settings.rag_model`, which is the *query* encoder:
# a Chroma collection locks to the dimension of its first insert, so the two
# names must never diverge. Change one, change the other.
TEXT_MODEL = "microsoft/harrier-oss-v1-270m"
# The Hugging Face commit of `TEXT_MODEL`, kept byte-identical to
# `Settings.rag_model_revision` for the same reason: a store embedded with one
# snapshot is queried with the other. A published knowledge base records both,
# and an import refuses a store embedded with anything else.
TEXT_MODEL_REVISION = "31de22b673913c7d658c0f03f792d77c2dcf8ebd"


# ---------------------------------------------------------------------------
# BM25 corpus
# ---------------------------------------------------------------------------
# The lexical half of hybrid retrieval (`vista_mcp_server.bm25`), written next
# to each ChromaDB store. Format v1 of `BM25Okapi.to_dict`, without the
# optional `tokens`: the MCP server tokenizes each chunk itself when it loads
# the file, so the tokenizer exists in one place only and the backend, which
# cannot import the MCP server's package, writes the same file.
BM25_CORPUS_FILENAME = "bm25_corpus.json"


def write_bm25_corpus(text_collection: Any, db_path: str | Path) -> int | None:
    """
    Write `<db_path>/bm25_corpus.json` from every chunk in `text_collection`,
    replacing the file in one step so a reader never sees half of it. An empty
    collection removes the file. Returns the number of chunks written, or None
    when the collection could not be read or the file not written (logged:
    hybrid retrieval then degrades to vector-only for this store).
    """
    path = Path(db_path) / BM25_CORPUS_FILENAME
    try:
        raw = text_collection.get(include=["documents"])
    except Exception as exc:  # noqa: BLE001
        log.warning("BM25: could not read text_chunks for corpus build: %s", exc)
        return None
    ids = raw.get("ids") or []
    docs = raw.get("documents") or []
    if len(ids) != len(docs):
        log.warning(
            "BM25: text_chunks is malformed (ids=%d docs=%d); skipping corpus build",
            len(ids), len(docs),
        )
        return None
    try:
        if not ids:
            path.unlink(missing_ok=True)
            return 0
        payload = {
            "version": 1,
            "k1": 1.5,
            "b": 0.75,
            "documents": [
                {"id": str(i), "text": str(d or "")} for i, d in zip(ids, docs)
            ],
        }
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("BM25: could not write corpus to %s: %s", path, exc)
        return None
    log.info("BM25: wrote corpus to %s (%d chunks)", path, len(ids))
    return len(ids)


# ---------------------------------------------------------------------------
# Azure OpenAI helper
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# LLM provider resolution
#
# The citation extractor must hit the same model the rest of the app uses.
# The chat route in `ui/app/api/chat/route.ts` resolves its provider in
# this order:
#
#   1. Azure OpenAI, if all three of AZURE_OPENAI_ENDPOINT,
#      AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT_NAME are set.
#   2. Otherwise generic OpenAI-compatible: OPENAI_BASE_URL +
#      OPENAI_API_KEY + OPENAI_MODEL. OPENAI_CHAT_URL can override the
#      computed URL. OPENAI_AUTH_MODE=api_key sends an `api-key:` header
#      instead of `Authorization: Bearer ...` (Azure-style).
#
# We mirror that here. Legacy variables (ENDPOINT_URL, DEPLOYMENT_NAME)
# are still honored as fallbacks for setups configured before this
# unification, but with a deprecation warning the first time they're
# used.
# ---------------------------------------------------------------------------

# Module-level guards so we only warn once per process.
_warned_legacy_vars = False


@dataclass(frozen=True)
class LLMCredentials:
    """
    An explicitly supplied inference credential for citation extraction.

    Everything below resolves the LLM from the process environment, which is
    right for a deployment configured through `.env` but cannot see a key the
    researcher typed into the settings modal -- that lives encrypted in their
    database row, and `vista_backend.agents.inference` is what knows how to
    resolve it. Passing one of these in lets the caller do that resolution and
    hand down the answer, so a user-created knowledge base gets citations
    instead of silently getting none.

    `api_key` is what makes an instance usable; with it unset the env chain
    runs as before. `disabled` says the caller decided there is nothing to
    extract with -- its provider has no model -- so the env chain must not
    run either.
    """

    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    disabled: bool = False

    @property
    def is_usable(self) -> bool:
        return bool(self.api_key)


def _parse_backend_model() -> tuple[str | None, str | None]:
    """
    Parse VISTA_BACKEND_MODEL (the canonical chat-agent config in
    .env.sample) into (provider, model). Returns (None, None) when
    unset or malformed.

    Examples:
        "azure:gpt-5"            -> ("azure", "gpt-5")
        "openai:claude-sonnet"   -> ("openai", "claude-sonnet")
        ""                       -> (None, None)
        "gpt-4o-mini"            -> (None, None)  # no provider prefix

    Lets the citation extractor reuse the chat agent's model config
    instead of requiring users to set a parallel set of env vars
    (AZURE_OPENAI_DEPLOYMENT_NAME / OPENAI_MODEL) that .env.sample
    never mentions.
    """
    bm = (os.getenv("VISTA_BACKEND_MODEL") or "").strip()
    if ":" not in bm:
        return (None, None)
    provider, _, model = bm.partition(":")
    provider = provider.strip().lower()
    model = model.strip()
    if not provider or not model:
        return (None, None)
    return (provider, model)


class _LLMConfig:
    """Resolved LLM configuration for a single citation-extraction call."""

    __slots__ = ("provider", "model", "client", "endpoint")

    def __init__(self, provider: str, model: str, client: Any, endpoint: str):
        # provider is "azure" or "openai" — informational, used for logging.
        self.provider = provider
        self.model = model
        self.client = client
        self.endpoint = endpoint


def _resolve_llm_config(
    timeout: float, credentials: "LLMCredentials | None" = None
) -> _LLMConfig:
    """
    Decide which LLM provider to use and build the corresponding client.
    See the comment block above for the resolution order.

    A usable `credentials` wins outright and skips that order entirely: the
    caller has already decided which endpoint and key to use -- typically from
    a user's settings row -- and re-deriving it from the environment could only
    contradict them.
    """
    global _warned_legacy_vars

    if credentials is not None and credentials.is_usable:
        base_url = (credentials.base_url or "https://api.openai.com/v1").rstrip("/")
        model = credentials.model or _parse_backend_model()[1] or "gpt-4o-mini"
        log.info(
            "  LLM provider: OpenAI-compatible (supplied) | base_url=%s | model=%s",
            base_url, model,
        )
        return _LLMConfig(
            provider="openai",
            model=model,
            client=OpenAI(
                base_url=base_url,
                api_key=credentials.api_key,
                timeout=timeout,
                max_retries=1,
            ),
            endpoint=base_url,
        )

    azure_endpoint = (os.getenv("AZURE_OPENAI_ENDPOINT") or "").rstrip("/")
    azure_key = os.getenv("AZURE_OPENAI_API_KEY") or ""
    azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME") or ""
    azure_api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")

    # Fall back to VISTA_BACKEND_MODEL="azure:<deployment>" if the
    # explicit env var is unset. This is what .env.sample documents
    # for the chat agent, and historically the citation extractor
    # ignored it — keeping the two paths in sync removes a footgun.
    bm_provider, bm_model = _parse_backend_model()
    if not azure_deployment and bm_provider == "azure" and bm_model:
        azure_deployment = bm_model

    # --- Azure path: all three required ---
    if azure_endpoint and azure_key and azure_deployment:
        log.info(
            "  LLM provider: Azure OpenAI | endpoint=%s | deployment=%s | api_version=%s",
            azure_endpoint, azure_deployment, azure_api_version,
        )
        client = AzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=azure_key,
            api_version=azure_api_version,
            timeout=timeout,
            max_retries=1,
        )
        return _LLMConfig(
            provider="azure",
            model=azure_deployment,
            client=client,
            endpoint=azure_endpoint,
        )

    # --- Legacy ENDPOINT_URL/DEPLOYMENT_NAME path (deprecated) ---
    legacy_endpoint = os.getenv("ENDPOINT_URL")
    legacy_deployment = os.getenv("DEPLOYMENT_NAME")
    if legacy_endpoint and legacy_deployment and azure_key:
        if not _warned_legacy_vars:
            log.warning(
                "DEPRECATED: ENDPOINT_URL and DEPLOYMENT_NAME are still being "
                "honored, but the canonical names are now AZURE_OPENAI_ENDPOINT "
                "and AZURE_OPENAI_DEPLOYMENT_NAME (matching the chat route). "
                "Please update your .env."
            )
            _warned_legacy_vars = True
        log.info(
            "  LLM provider: Azure OpenAI (via legacy vars) | endpoint=%s | deployment=%s",
            legacy_endpoint, legacy_deployment,
        )
        client = AzureOpenAI(
            azure_endpoint=legacy_endpoint.rstrip("/"),
            api_key=azure_key,
            api_version=azure_api_version,
            timeout=timeout,
            max_retries=1,
        )
        return _LLMConfig(
            provider="azure",
            model=legacy_deployment,
            client=client,
            endpoint=legacy_endpoint,
        )

    # --- Generic OpenAI-compatible path ---
    base_url = (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip().strip("\"'")
    model = os.getenv("OPENAI_MODEL") or ""
    # Same fallback as the azure path: if OPENAI_MODEL is unset, try
    # VISTA_BACKEND_MODEL="openai:<model>". Falls through to the
    # historical gpt-4o-mini default only when neither is set.
    if not model and bm_provider == "openai" and bm_model:
        model = bm_model
    if not model:
        model = "gpt-4o-mini"
    chat_url_override = (os.getenv("OPENAI_CHAT_URL") or "").strip().rstrip("/")
    auth_mode = (os.getenv("OPENAI_AUTH_MODE") or "bearer").lower()

    if not api_key:
        raise RuntimeError(
            "No LLM credentials configured. Set either:\n"
            "  - AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY + AZURE_OPENAI_DEPLOYMENT_NAME, OR\n"
            "  - OPENAI_API_KEY (with optional OPENAI_BASE_URL / OPENAI_MODEL)\n"
            "in your .env. VISTA_BACKEND_MODEL=\"azure:<deployment>\" or "
            "\"openai:<model>\" is also accepted as a fallback for the "
            "deployment/model name. The citation extractor uses the "
            "same provider as the chat agent."
        )

    # If the user's "OpenAI-compatible" endpoint is actually Azure
    # (auth_mode=api_key), use AzureOpenAI which handles that header
    # correctly. The chat_url_override + auth_mode=api_key combo is the
    # "Azure-but-call-it-OpenAI" path documented in .env.sample.
    if auth_mode == "api_key" and chat_url_override:
        log.info(
            "  LLM provider: Azure OpenAI (via OPENAI_CHAT_URL override) | url=%s | model=%s",
            chat_url_override, model,
        )
        # OPENAI_CHAT_URL points directly at the deployment's chat
        # completions endpoint — extract the bits AzureOpenAI wants.
        # Format: https://<host>/openai/deployments/<deployment>/chat/completions?api-version=...
        # We can't easily reconstruct azure_endpoint from this, so use
        # OpenAI() with a custom default_headers instead — it just sends
        # whatever URL we pass and the api-key header.
        client = OpenAI(
            base_url=chat_url_override.split("/openai/")[0] + "/openai/v1"
            if "/openai/" in chat_url_override
            else base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=1,
            default_headers={"api-key": api_key},
        )
        return _LLMConfig(
            provider="openai",
            model=model,
            client=client,
            endpoint=chat_url_override,
        )

    log.info(
        "  LLM provider: OpenAI-compatible | base_url=%s | model=%s | auth=%s",
        base_url, model, auth_mode,
    )
    client = OpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=timeout,
        max_retries=1,
    )
    return _LLMConfig(
        provider="openai",
        model=model,
        client=client,
        endpoint=base_url,
    )


# Per-model cache of which reasoning_effort value works. Populated
# lazily — the first call to a given model tries "minimal" (best for
# extraction tasks per OpenAI's recommendations); if the API rejects
# it (some variants like gpt-5.1-codex and gpt-5-nano only support
# low/medium/high), we cache the fallback. Persists for the lifetime
# of the indexer subprocess.
#
# Values: "minimal" | "low" | "none" (never send the kwarg).
_REASONING_EFFORT_CACHE: Dict[str, str] = {}


def _is_reasoning_model(model: str) -> bool:
    """
    Heuristic: does this model name look like an OpenAI/Azure reasoning
    model (GPT-5 family or o-series)? Reasoning models burn tokens on
    invisible internal chain-of-thought before producing visible output,
    so we have to budget more generously and (where supported) pass
    `reasoning_effort` to keep the budget bounded.

    Name-based detection is intentionally fragile — when the model
    name doesn't include a recognizable family hint we err on the side
    of NOT sending `reasoning_effort` (which would cause a 400 on a
    non-reasoning model) and rely on the bumped max_tokens budget alone.
    """
    name = model.lower()
    # Match the family roots — strip away any deployment-name suffixes
    # the user may have configured (e.g. "gpt-5-prod", "gpt-5-chat").
    return (
        name.startswith("gpt-5")
        or name.startswith("o1")
        or name.startswith("o3")
        or name.startswith("o4")
    )


# ---------------------------------------------------------------------------
# LLM call wrapper
# ---------------------------------------------------------------------------
def _call_with_reasoning_fallback(
    config: "_LLMConfig",
    base_kwargs: Dict[str, Any],
) -> Any:
    """
    Invoke a reasoning model with `reasoning_effort`, falling back to
    less-aggressive settings if the model rejects the value.

    OpenAI's `reasoning_effort` accepts "minimal" / "low" / "medium" /
    "high", but support varies by model:

      - GPT-5 (the original): all four
      - GPT-5-Nano, GPT-5.1-codex, etc.: only low / medium / high
      - Some Azure-hosted variants: kwarg is unrecognized entirely

    Trying "minimal" first is the best choice for extraction tasks
    (OpenAI's docs explicitly recommend it for "deterministic,
    lightweight tasks"). On rejection we fall back to "low" and cache
    the result so subsequent calls skip the failed attempt.

    On models that reject the kwarg entirely we cache "none" and stop
    sending it, accepting that we'll spend more reasoning tokens (which
    is why max_tokens is set generously upstream).
    """
    cached = _REASONING_EFFORT_CACHE.get(config.model)
    candidates: List[str]
    if cached is None:
        candidates = ["minimal", "low", "none"]
    elif cached == "none":
        candidates = ["none"]
    else:
        candidates = [cached]

    last_exc: Optional[BaseException] = None
    for effort in candidates:
        kwargs = dict(base_kwargs)
        if effort != "none":
            kwargs["reasoning_effort"] = effort
        try:
            completion = config.client.chat.completions.create(**kwargs)
            # Cache the first level that worked so we don't keep trying
            # rejected values on every paper.
            if cached is None:
                _REASONING_EFFORT_CACHE[config.model] = effort
                if effort != "minimal":
                    log.info(
                        "  Reasoning effort %r not accepted by %s; "
                        "using %r for subsequent calls.",
                        candidates[0], config.model, effort,
                    )
            return completion
        except BadRequestError as exc:
            msg = str(exc)
            # Recognize the two error shapes the API uses:
            #   "Unsupported value: 'minimal' is not supported with the '...' model"
            #   "Unknown parameter: 'reasoning_effort'"
            # Anything else gets re-raised — those are real failures
            # (rate limit, content filter, malformed request, etc.).
            unsupported_value = (
                "Unsupported value" in msg
                or f"'{effort}' is not supported" in msg
            )
            unknown_param = (
                "reasoning_effort" in msg
                and ("Unknown parameter" in msg or "Unrecognized" in msg
                     or "unexpected keyword" in msg)
            )
            if unsupported_value or unknown_param:
                log.info(
                    "  reasoning_effort=%r rejected for %s — %s",
                    effort, config.model,
                    "trying next fallback" if effort != candidates[-1] else "giving up",
                )
                last_exc = exc
                continue
            raise

    # All candidates exhausted. The last exception is the most relevant
    # one to surface.
    if last_exc is not None:
        raise last_exc
    # Shouldn't reach here, but be defensive.
    raise RuntimeError(
        f"Failed to find a working reasoning_effort for model {config.model}"
    )


def send_prompt_to_chatgpt(
    prompt: str,
    *,
    max_tokens: int = 4096,
    timeout: float = 60.0,
    credentials: "LLMCredentials | None" = None,
) -> str:
    """
    Send a prompt to the configured LLM and return the response text.

    Provider resolution mirrors the chat route — Azure OpenAI if those
    env vars are set, otherwise generic OpenAI-compatible. See
    `_resolve_llm_config` for the full resolution order.

    `max_tokens` is the maximum number of completion tokens the model
    may emit. Default 4096 is intentionally generous because reasoning
    models (GPT-5 family, o-series) consume tokens on internal
    chain-of-thought BEFORE producing visible output. With max=1024 a
    GPT-5 model can burn the entire budget on reasoning and return an
    empty string with finish_reason="length", which looks exactly like
    a hang from the caller's perspective.

    `timeout` is the per-request wall-clock cap, in seconds. Default
    60s is generous for citation extraction (~5-15s normal, ~30s under
    load). Without this cap, a hung connection could leave the indexer
    subprocess waiting indefinitely.

    Logs each step at INFO level so the user can watch the terminal and
    see exactly where time is being spent. Also logs usage details
    (prompt_tokens / completion_tokens / reasoning_tokens) on the
    response so empty-output failures are diagnosable.
    """
    config = _resolve_llm_config(timeout, credentials)

    prompt_chars = len(prompt)
    is_reasoning = _is_reasoning_model(config.model)

    log.info(
        "  → LLM request: provider=%s model=%s endpoint=%s "
        "prompt_chars=%d max_tokens=%d timeout=%.0fs reasoning=%s",
        config.provider, config.model, config.endpoint,
        prompt_chars, max_tokens, timeout, is_reasoning,
    )

    # Build the base call kwargs. reasoning_effort is added only for
    # reasoning models, and we may need to retry if the chosen level
    # isn't supported by this specific model variant — see
    # _call_with_reasoning_fallback below.
    base_kwargs: Dict[str, Any] = {
        "model": config.model,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            }
        ],
        "max_completion_tokens": max_tokens,
        "stop": None,
        "stream": False,
        "timeout": timeout,
    }

    start = time.monotonic()
    try:
        if is_reasoning:
            completion = _call_with_reasoning_fallback(config, base_kwargs)
        else:
            completion = config.client.chat.completions.create(**base_kwargs)
        elapsed = time.monotonic() - start

        # Pull out the visible text and the diagnostics we'll log.
        choice = completion.choices[0] if completion.choices else None
        text = (choice.message.content if choice and choice.message else "") or ""
        finish_reason = getattr(choice, "finish_reason", None) if choice else None

        usage = getattr(completion, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
        # reasoning_tokens is nested under completion_tokens_details for
        # OpenAI/Azure reasoning models. May be absent on classic models.
        reasoning_tokens = None
        details = getattr(usage, "completion_tokens_details", None) if usage else None
        if details is not None:
            reasoning_tokens = getattr(details, "reasoning_tokens", None)

        log.info(
            "  ← LLM response: provider=%s model=%s elapsed=%.2fs "
            "response_chars=%d finish_reason=%s prompt_tokens=%s "
            "completion_tokens=%s reasoning_tokens=%s",
            config.provider, config.model, elapsed, len(text),
            finish_reason, prompt_tokens, completion_tokens, reasoning_tokens,
        )

        # If the call succeeded HTTP-wise but came back empty, give the
        # user something actionable. The most common cause is a
        # reasoning model burning the entire token budget on hidden
        # chain-of-thought.
        if not text:
            if (
                finish_reason == "length"
                and reasoning_tokens
                and completion_tokens
                and reasoning_tokens >= completion_tokens * 0.8
            ):
                log.warning(
                    "  ⚠ Empty response: model spent %d/%d completion tokens "
                    "on reasoning, leaving none for visible output. "
                    "Try raising max_tokens above %d, lowering reasoning "
                    "effort, or switching to a non-reasoning model "
                    "(e.g. gpt-4o-mini, claude-opus).",
                    reasoning_tokens, completion_tokens, max_tokens,
                )
            elif finish_reason == "length":
                log.warning(
                    "  ⚠ Empty response: hit max_tokens=%d before model "
                    "produced any visible output.",
                    max_tokens,
                )
            elif finish_reason == "content_filter":
                log.warning(
                    "  ⚠ Empty response: response was filtered by the "
                    "content policy."
                )
            else:
                log.warning(
                    "  ⚠ Empty response: model returned no text "
                    "(finish_reason=%s).",
                    finish_reason,
                )

        return text

    except BadRequestError as e:
        elapsed = time.monotonic() - start
        msg = str(e)
        if "content_filter" in msg or "ResponsibleAIPolicyViolation" in msg:
            log.warning(
                "  ⚠ Content filter triggered after %.2fs — returning empty string.",
                elapsed,
            )
            return ""
        log.error("  ✗ BadRequestError after %.2fs: %s", elapsed, msg)
        raise

    except Exception as e:  # noqa: BLE001
        elapsed = time.monotonic() - start
        log.error(
            "  ✗ LLM call failed after %.2fs: %s: %s",
            elapsed, type(e).__name__, e,
        )
        raise


def parse_citation_json(raw: str) -> Optional[dict]:
    """Try to parse the model response as JSON."""
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        log.warning("Could not parse citation JSON:\n%s", raw[:300])
        return None


# ---------------------------------------------------------------------------
# Per-format extraction
# ---------------------------------------------------------------------------
# One extractor per file format, chosen by lowercase suffix. Each takes a path
# (and an optional description, used only by tables) and returns passages of
# the shape `{"text": str, "metadata": {...}}`. The extractors are plain
# functions: they need neither the embedding model nor Chroma.
#
# Metadata common to every passage: `source` (file name), `type` ("text"),
# `kind` ("pdf" | "csv" | "tsv" | "text" | "markdown") and `chunk_id`. The
# location field is `page` for PDFs, `rows` for tables ("summary" or "51-100",
# 1-based inclusive data-row numbers) and just `chunk_id` for text.

TEXT_CHUNK_WORDS = 512
TEXT_CHUNK_OVERLAP = 50
CSV_WINDOW_ROWS = 50          # data rows per window
CSV_MAX_WINDOWS = 200         # windows indexed per file; later rows are only counted
CSV_SUMMARY_SAMPLE_ROWS = 5
CSV_MAX_SUMMARY_COLUMNS = 30  # columns listed in full in the summary
DESCRIPTION_TEXT_WORDS = 2000  # text sent to the description call for txt/md

DESCRIPTION_PROMPT = """\
Below is {what} named "{filename}". In 1 to 3 plain sentences, describe what \
it contains and what it could be used for. Do not invent details that are not \
in the text. Reply with the description only.

--- BEGIN ---
{text}
--- END ---
"""


def chunk_words(
    text: str,
    chunk_size: int = TEXT_CHUNK_WORDS,
    overlap: int = TEXT_CHUNK_OVERLAP,
) -> List[str]:
    """Split `text` into overlapping windows of `chunk_size` words."""
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size - overlap):
        chunk = " ".join(words[i : i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
    return chunks


def _truncate_words(text: str, limit: int = TEXT_CHUNK_WORDS) -> str:
    """Keep whole leading lines of `text` while they fit in `limit` words."""
    out: list[str] = []
    used = 0
    for line in text.split("\n"):
        words = line.split()
        if used + len(words) > limit:
            remaining = limit - used
            if remaining > 0 and words:
                out.append(" ".join(words[:remaining]))
            break
        out.append(line)
        used += len(words)
    return "\n".join(out)


def _extract_pdf(path: Path, description: Optional[str] = None) -> List[Dict[str, Any]]:
    """PDF pages, chunked: metadata `{source, page, chunk_id, kind, type}`."""
    doc = fitz.open(path)
    passages: List[Dict[str, Any]] = []
    try:
        for page_num in range(len(doc)):
            text = doc[page_num].get_text()
            for idx, chunk in enumerate(chunk_words(text)):
                passages.append({
                    "text": chunk,
                    "metadata": {
                        "source": path.name,
                        "page": page_num,
                        "chunk_id": idx,
                        "type": "text",
                        "kind": "pdf",
                    },
                })
    finally:
        doc.close()
    return passages


def read_text_file(path: Path) -> str:
    """Read a text file as UTF-8, tolerating a BOM and bad bytes."""
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _extract_text_file(
    path: Path, description: Optional[str] = None
) -> List[Dict[str, Any]]:
    """TXT / MD: word chunks, metadata `{source, kind, chunk_id, type}`."""
    kind = "markdown" if path.suffix.lower() == ".md" else "text"
    return [
        {
            "text": chunk,
            "metadata": {
                "source": path.name,
                "chunk_id": idx,
                "type": "text",
                "kind": kind,
            },
        }
        for idx, chunk in enumerate(chunk_words(read_text_file(path)))
    ]


@dataclass
class _Table:
    columns: List[str]
    types: List[str]
    rows: List[List[str]]   # at most CSV_WINDOW_ROWS * CSV_MAX_WINDOWS
    row_count: int          # every data row in the file
    delimiter: str


def _infer_type(values: List[str]) -> str:
    """int, then float, otherwise text; blanks are ignored."""
    present = [v.strip() for v in values if v.strip()]
    if not present:
        return "text"
    for caster, name in ((int, "int"), (float, "float")):
        try:
            for v in present:
                caster(v)
            return name
        except ValueError:
            continue
    return "text"


def _read_table(path: Path) -> _Table:
    fallback = "\t" if path.suffix.lower() == ".tsv" else ","
    keep = CSV_WINDOW_ROWS * CSV_MAX_WINDOWS
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        sample = f.read(8192)
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
        except csv.Error:
            delimiter = fallback
        f.seek(0)
        reader = csv.reader(f, delimiter=delimiter)
        columns: List[str] = []
        rows: List[List[str]] = []
        count = 0
        for rec in reader:
            if not rec or not any(c.strip() for c in rec):
                continue
            if not columns:
                columns = [c.strip() for c in rec]
                continue
            count += 1
            if len(rows) < keep:
                rows.append(rec)
    types = [
        _infer_type([r[i] for r in rows if i < len(r)]) for i in range(len(columns))
    ]
    return _Table(columns, types, rows, count, delimiter)


def _render_rows(rows: List[List[str]], delimiter: str) -> str:
    buf = io.StringIO()
    csv.writer(buf, delimiter=delimiter, lineterminator="\n").writerows(rows)
    return buf.getvalue().rstrip("\n")


def _extract_table(path: Path, description: Optional[str] = None) -> List[Dict[str, Any]]:
    """CSV / TSV: one summary passage, then header-prefixed row windows."""
    kind = "tsv" if path.suffix.lower() == ".tsv" else "csv"
    table = _read_table(path)
    base = {"source": path.name, "type": "text", "kind": kind}

    shown = list(zip(table.columns, table.types))[:CSV_MAX_SUMMARY_COLUMNS]
    col_line = ", ".join(f"{n} ({t})" for n, t in shown)
    extra = len(table.columns) - len(shown)
    if extra > 0:
        col_line += f", and {extra} more columns"
    head = [
        f"File: {path.name} ({kind.upper()} table)",
        f"Columns ({len(table.columns)}): {col_line}",
        f"Data rows: {table.row_count}",
    ]
    if description:
        head.append(f"Description: {description}")
    if table.row_count > len(table.rows):
        head.append(
            f"Note: only the first {len(table.rows)} rows are indexed for search; "
            f"the file has {table.row_count} data rows in total."
        )
    sample = _render_rows(
        [table.columns] + table.rows[:CSV_SUMMARY_SAMPLE_ROWS], table.delimiter
    )
    head_text = "\n".join(head)
    budget = max(0, TEXT_CHUNK_WORDS - len(head_text.split()) - 6)
    summary = (
        f"{head_text}\nFirst {CSV_SUMMARY_SAMPLE_ROWS} rows:\n"
        + _truncate_words(sample, budget)
    )
    passages: List[Dict[str, Any]] = [{
        "text": summary,
        "metadata": {
            **base, "rows": "summary", "chunk_id": 0, "row_count": table.row_count,
        },
    }]
    for w, start in enumerate(range(0, len(table.rows), CSV_WINDOW_ROWS), start=1):
        window = table.rows[start : start + CSV_WINDOW_ROWS]
        text = _truncate_words(_render_rows([table.columns] + window, table.delimiter))
        passages.append({
            "text": text,
            "metadata": {
                **base,
                "rows": f"{start + 1}-{start + len(window)}",
                "chunk_id": w,
            },
        })
    return passages


# Suffix (lowercase) -> extractor. This is the one list of formats VISTA accepts
# into a knowledge base; the backend imports `ACCEPTED_SUFFIXES` from here.
FORMAT_EXTRACTORS: Dict[str, Any] = {
    ".pdf": _extract_pdf,
    ".csv": _extract_table,
    ".tsv": _extract_table,
    ".txt": _extract_text_file,
    ".md": _extract_text_file,
}
ACCEPTED_SUFFIXES: tuple[str, ...] = tuple(FORMAT_EXTRACTORS)

# Citation-row `record` value for files that carry a description, not a citation.
SUFFIX_KIND = {
    ".pdf": "pdf", ".csv": "csv", ".tsv": "tsv", ".txt": "text", ".md": "markdown",
}
DESCRIPTION_RECORDS = {
    "csv": "dataset", "tsv": "dataset", "text": "note", "markdown": "note",
}


def extractor_for(path: Path):
    """The extractor for `path`'s suffix (case-insensitive), or None."""
    return FORMAT_EXTRACTORS.get(Path(path).suffix.lower())


# ---------------------------------------------------------------------------
# TextRAG
# ---------------------------------------------------------------------------
class TextRAG:
    def __init__(
        self,
        pdf_folder: str,
        db_path: str = "./chroma_db",
        # See `TEXT_MODEL` and `TEXT_MODEL_REVISION` above.
        text_model: str = TEXT_MODEL,
        text_model_revision: str = TEXT_MODEL_REVISION,
        force_reindex: bool = False,
        extract_citations: bool = True,
        citation_max_pages: int = 5,
        citation_max_chars: int = 12_000,
        llm_credentials: "LLMCredentials | None" = None,
    ):
        """
        Initialize text-only RAG system with optional citation extraction.

        Args:
            pdf_folder:         Path to folder containing PDFs.
            db_path:            Path to store ChromaDB database.
            text_model:         SentenceTransformers model for text embeddings.
            text_model_revision: Hugging Face commit of `text_model` to load.
            force_reindex:      If True, rebuild database even if it exists.
            extract_citations:  If True, call Azure OpenAI to extract citation
                                metadata for each PDF and store it in a
                                separate ChromaDB collection.
            citation_max_pages: Max front-matter pages to read per PDF for
                                citation extraction.
            citation_max_chars: Max characters of front-matter text to send
                                to the LLM.
            llm_credentials:    Inference credential for citation extraction.
                                When omitted, resolved from the environment.
                                See `LLMCredentials`.
        """
        self.pdf_folder = Path(pdf_folder)
        self.db_path = db_path
        self.force_reindex = force_reindex
        self.extract_citations = extract_citations
        self.citation_max_pages = citation_max_pages
        self.citation_max_chars = citation_max_chars
        self.llm_credentials = llm_credentials

        # Text embedding model.
        #
        # `device=None` lets sentence-transformers pick the best available
        # accelerator -- cuda, then mps, then cpu. Previously pinned to cpu,
        # which measured at 28s per batch of 8 chunks against 0.5s on mps:
        # hours versus minutes to index the molten-salt corpus, paid on every
        # packaging build and on any first run that has to index. Set
        # `VISTA_EMBED_DEVICE` to force one (e.g. `cpu`) if an accelerator
        # misbehaves. The same variable pins the *query* encoder in
        # `vista_mcp_server.rag_mcp`; the device changes only how fast the
        # vectors are computed, not what they are, so the two need not agree.
        device = os.environ.get("VISTA_EMBED_DEVICE") or None
        log.info(
            "Loading text model: %s@%s (device=%s)",
            text_model,
            text_model_revision[:12],
            device or "auto",
        )
        self.text_encoder = SentenceTransformer(
            text_model, revision=text_model_revision, device=device
        )

        self.client = chromadb.PersistentClient(
            path=db_path,
            # telemetry complicates process cleanup
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.db_exists = self._check_database_exists()

        # Collections
        # Both collections are created without an `embedding_function`, so
        # Chroma attaches its default (`ONNXMiniLM_L6_V2`). That default
        # downloads an ONNX archive from a public S3 bucket -- but only inside
        # its `__call__`, which never fires because every write below passes
        # `embeddings=` and every read passes `query_embeddings=`, computed by
        # `embed_text` from `self.text_model`. Do not add a call that omits
        # them: it would reach the network on a machine meant to work offline,
        # and write 384-dimension vectors into a 640-dimension collection.
        self.text_collection = self.client.get_or_create_collection(
            name="text_chunks",
            metadata={"hnsw:space": "cosine"},
        )
        self.citation_collection = self.client.get_or_create_collection(
            name="citations",
            metadata={"hnsw:space": "cosine"},
        )

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------
    def _check_database_exists(self) -> bool:
        db_path = Path(self.db_path)
        if not db_path.exists():
            return False
        try:
            for collection in self.client.list_collections():
                if collection.count() > 0:
                    log.info(
                        "Existing DB at %s – collection '%s' has %d items",
                        self.db_path, collection.name, collection.count(),
                    )
                    return True
        except Exception as e:
            log.error("Error checking database: %s", e)
        return False

    # ------------------------------------------------------------------
    # PDF text extraction
    # ------------------------------------------------------------------
    def extract_text_from_pdf(self, pdf_path: Path) -> List[Dict[str, Any]]:
        """Extract text chunks from entire PDF with metadata."""
        return _extract_pdf(Path(pdf_path))

    def _extract_front_matter_text(self, pdf_path: Path) -> str:
        """Extract text from the first few pages for citation extraction."""
        doc = fitz.open(pdf_path)
        parts: list[str] = []
        for page in doc[: self.citation_max_pages]:
            text = page.get_text()
            if text:
                parts.append(text)
        doc.close()
        return "\n\n".join(parts)[: self.citation_max_chars]

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 512, overlap: int = 50) -> List[str]:
        return chunk_words(text, chunk_size, overlap)

    # ------------------------------------------------------------------
    # Citation extraction via Azure OpenAI
    # ------------------------------------------------------------------
    def _extract_citation(self, pdf_path: Path) -> Optional[dict]:
        """Call the configured LLM to pull structured citation metadata."""
        log.info("Citation extraction start: %s", pdf_path.name)

        # Step 1: pull text from the front matter.
        t0 = time.monotonic()
        front_text = self._extract_front_matter_text(pdf_path)
        log.info(
            "  Front-matter extracted: %d chars in %.2fs",
            len(front_text), time.monotonic() - t0,
        )
        if not front_text.strip():
            log.warning("  No front-matter text — skipping citation for %s", pdf_path.name)
            return None

        # Step 2: send to LLM.
        # max_tokens=4096 leaves comfortable room on reasoning models —
        # at "low" reasoning effort GPT-5 typically spends 500-2000
        # tokens on internal CoT, so we need a budget well above that
        # for the JSON output to fit. With max=1024 the model burns the
        # entire budget reasoning and returns an empty string.
        prompt = CITATION_PROMPT.format(
            text=front_text,
            filename=pdf_path.name,
        )
        try:
            raw = send_prompt_to_chatgpt(
                prompt, max_tokens=4096, credentials=self.llm_credentials
            )
        except Exception as exc:  # noqa: BLE001
            log.error("  Citation API call failed for %s: %s", pdf_path.name, exc)
            return None

        # Step 3: parse the response.
        if not raw:
            log.warning("  LLM returned empty response for %s", pdf_path.name)
            return None

        # Always log a truncated view of the raw response, even on
        # success, so the user can verify the model is returning
        # well-formed JSON (and not, e.g., an error blob, an HTML page
        # from a misconfigured endpoint, or a markdown-fenced block).
        # Truncate generously — full JSON for a paper is ~1-3KB.
        preview = raw[:1500] + ("..." if len(raw) > 1500 else "")
        log.info(
            "  LLM response preview (%d chars) for %s:\n%s",
            len(raw), pdf_path.name, preview,
        )

        parsed = parse_citation_json(raw)
        if parsed is None:
            log.warning(
                "  Could not parse LLM response as JSON for %s (response was %d chars)",
                pdf_path.name, len(raw),
            )
        else:
            log.info(
                "  Citation parsed for %s: title=%r authors=%d journal=%r year=%r doi=%r",
                pdf_path.name,
                (parsed.get("title") or "")[:80],
                len(parsed.get("authors") or []),
                (parsed.get("journal") or "")[:60],
                parsed.get("year") or "",
                parsed.get("doi") or "",
            )
        return parsed

    # ------------------------------------------------------------------
    # Description for non-PDF files
    # ------------------------------------------------------------------
    def _describe_file(self, path: Path, kind: str, text: str) -> Optional[str]:
        """
        One LLM call: a 1-3 sentence plain description of a CSV/TSV/TXT/MD
        file, through the same provider resolution and reasoning-model
        fallback as citations. Returns None when the model gives nothing.
        """
        what = "the summary of a table" if kind in ("csv", "tsv") else "a text file"
        prompt = DESCRIPTION_PROMPT.format(what=what, filename=path.name, text=text)
        raw = send_prompt_to_chatgpt(
            prompt, max_tokens=4096, credentials=self.llm_credentials
        )
        description = " ".join((raw or "").split())
        return description[:1000] or None

    def _descriptions_enabled(self) -> bool:
        creds = self.llm_credentials
        return bool(self.extract_citations) and not (
            creds is not None and creds.disabled
        )

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------
    def embed_text(
        self, texts: List[str], *, prompt: Optional[str] = None
    ) -> List[List[float]]:
        """
        Encode `texts`. `prompt` defaults to None, which is what every
        indexing call wants: the model card says documents take no
        instruction. Query callers pass `query_prompt(...)`.
        """
        embeddings = self.text_encoder.encode(
            texts, prompt=prompt, convert_to_numpy=True
        )
        return embeddings.tolist()

    def embed_text_batched(
        self,
        texts: List[str],
        *,
        batch_size: int = 32,
        progress_cb: Optional[Any] = None,
    ) -> List[List[float]]:
        """
        Encode `texts` in batches of `batch_size`, calling `progress_cb`
        between batches with `(processed, total)` so callers can drive a
        progress bar. Returns the same shape as `embed_text`.

        Used by `index_single_file` to surface per-chunk progress during
        the (otherwise opaque) sentence-transformers encode step, which
        dominates wall time once the citation LLM step is past.
        """
        if not texts:
            return []
        out: List[List[float]] = []
        total = len(texts)
        for start in range(0, total, batch_size):
            batch = texts[start : start + batch_size]
            embeddings = self.text_encoder.encode(batch, convert_to_numpy=True)
            out.extend(embeddings.tolist())
            if progress_cb is not None:
                try:
                    progress_cb(min(start + len(batch), total), total)
                except Exception:  # noqa: BLE001
                    # Progress reporting must not break indexing.
                    pass
        return out

    # ------------------------------------------------------------------
    # Citation metadata → flat string dict for ChromaDB
    # ------------------------------------------------------------------
    @staticmethod
    def _citation_to_metadata(citation: dict, filename: str) -> dict:
        """
        Convert a citation dict into a flat metadata dict that ChromaDB
        can store (string / int / float values only).
        """
        meta: dict[str, Any] = {"source": filename, "type": "citation"}
        for field in CITATION_FIELDS:
            value = citation.get(field)
            if value is None:
                meta[field] = ""
            elif isinstance(value, list):
                # Store lists as JSON strings
                meta[field] = json.dumps(value, ensure_ascii=False)
            else:
                meta[field] = str(value)
        return meta

    @staticmethod
    def _citation_to_document(citation: dict) -> str:
        """
        Build a searchable text representation of the citation so that
        the citation collection is queryable via semantic search.
        """
        parts: list[str] = []
        if citation.get("title"):
            parts.append(citation["title"])
        if citation.get("authors"):
            parts.append("Authors: " + ", ".join(citation["authors"]))
        if citation.get("abstract"):
            parts.append(citation["abstract"])
        if citation.get("journal"):
            parts.append(f"Journal: {citation['journal']}")
        if citation.get("year"):
            parts.append(f"Year: {citation['year']}")
        if citation.get("doi"):
            parts.append(f"DOI: {citation['doi']}")
        if citation.get("keywords"):
            parts.append("Keywords: " + ", ".join(citation["keywords"]))
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------
    def index_single_file(
        self,
        pdf_path: Path,
        progress_cb: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Index a single file of any accepted format (see `FORMAT_EXTRACTORS`).

        PDFs get citation extraction. CSV, TSV, TXT and MD get a short LLM
        description instead, stored as the file's row in the citation
        collection (`record` "dataset" or "note") and, for tables, also put
        into the summary passage before it is embedded.

        Order of operations (rev. 2026-05-08):
            1. Citation metadata (Azure OpenAI on the front-matter text) —
               does the slow, network-bound work first so failures surface
               before we've polluted chroma with orphan chunks.
            2. Text chunks (PyMuPDF + sentence-transformers + chroma upsert).
            3. Citation row upsert (after #2 so the citation collection is
               only updated when we actually have text to back it).

        If citation extraction fails or is disabled, we still index the
        text chunks — the user keeps semantic search even when the LLM
        side is unavailable. They just won't see auto-populated titles.

        `progress_cb`, if given, is called at each milestone with a dict
        like `{"phase": "citation"|"extracting_text"|"embedding_chunks"
        |"upserting_chunks"|"done", "filename": ...}`. The
        "embedding_chunks" phase additionally includes
        `chunk_processed` and `chunk_total` so a UI can render
        sub-progress within a single document's embed step (which
        dominates wall time on CPU). Used by the streaming indexer to
        push events to the UI.

        Returns a status dict:
            {
                "filename": "<basename>",
                "status": "indexed" | "skipped" | "failed",
                "error": "..." | None,
                "chunk_count": <int>,
                "citation": { ...citation fields or None... },
                "citation_status": "extracted" | "failed" | "disabled" | "skipped",
            }

        `skipped` (for the whole result) means the PDF was already in the
        DB and `force_reindex` was False.
        """
        result: Dict[str, Any] = {
            "filename": pdf_path.name,
            "status": "failed",
            "error": None,
            "chunk_count": 0,
            "citation": None,
            "citation_status": "disabled" if not self.extract_citations else "pending",
            "citation_error": None,
        }
        extractor = extractor_for(pdf_path)
        is_pdf = pdf_path.suffix.lower() == ".pdf"
        file_kind = SUFFIX_KIND.get(pdf_path.suffix.lower(), "")
        description: Optional[str] = None
        if not is_pdf and not self._descriptions_enabled():
            result["citation_status"] = "disabled"

        def _emit(phase: str, **extra: Any) -> None:
            if progress_cb is None:
                return
            try:
                payload = {"phase": phase, "filename": pdf_path.name}
                payload.update(extra)
                progress_cb(payload)
            except Exception:  # noqa: BLE001
                # Progress reporting must not break indexing.
                pass

        # Idempotency: a file is identified by its `source` metadata (the full
        # file name), not by id, so stores indexed under older id schemes
        # still skip correctly. Skip unless we're force-reindexing.
        filename = pdf_path.name
        cite_id = f"{filename}_citation"
        if not self.force_reindex:
            try:
                existing = self.text_collection.get(
                    where={"source": filename}, limit=1
                )
                if existing and existing.get("ids"):
                    result["status"] = "skipped"
                    cites = self.citation_collection.get(
                        where={"source": filename}, limit=1
                    )
                    metas = (cites or {}).get("metadatas") or []
                    if metas:
                        result["citation"] = metas[0]
                        result["citation_status"] = "skipped"
                    _emit("done", status="skipped")
                    return result
            except Exception:
                pass

        try:
            # --- (1) Citation metadata FIRST ---
            # The slow, network-bound step. If Azure OpenAI is hung or
            # unreachable, we want to know now — not after we've already
            # written hundreds of text chunks.
            if extractor is None:
                raise ValueError(
                    f"Unsupported file type: {pdf_path.suffix or pdf_path.name}"
                )
            citation = None
            if not is_pdf:
                # Non-PDF files: one description call instead of a citation.
                # Tables send their deterministic summary passage, text files
                # their first ~2,000 words. Failure never blocks indexing.
                if self._descriptions_enabled():
                    _emit("citation")
                    try:
                        if file_kind in ("csv", "tsv"):
                            sample = extractor(pdf_path)[0]["text"]
                        else:
                            sample = " ".join(
                                read_text_file(pdf_path).split()[:DESCRIPTION_TEXT_WORDS]
                            )
                        if sample.strip():
                            description = self._describe_file(
                                pdf_path, file_kind, sample
                            )
                        if description:
                            result["citation_status"] = "extracted"
                        else:
                            result["citation_status"] = "failed"
                            result["citation_error"] = (
                                "LLM returned no description."
                            )
                    except Exception as e:  # noqa: BLE001
                        log.warning(
                            "  ✗ Description failed for %s: %s", pdf_path.name, e
                        )
                        result["citation_status"] = "failed"
                        err_str = str(e)
                        if len(err_str) > 500:
                            err_str = err_str[:497] + "..."
                        result["citation_error"] = err_str
                        description = None
            elif self.extract_citations:
                _emit("citation")
                try:
                    citation = self._extract_citation(pdf_path)
                    if citation:
                        result["citation_status"] = "extracted"
                    else:
                        result["citation_status"] = "failed"
                        result["citation_error"] = (
                            "LLM returned no parseable citation. Check the "
                            "indexer logs for the LLM response shape."
                        )
                        log.warning(
                            "  ✗ Citation extraction returned no result for %s",
                            pdf_path.name,
                        )
                except Exception as e:  # noqa: BLE001
                    # Don't fail the whole PDF over a citation hiccup —
                    # text chunks are still useful. Record the partial
                    # success and continue.
                    log.warning(
                        "  ✗ Citation extraction failed for %s: %s",
                        pdf_path.name, e,
                    )
                    result["citation_status"] = "failed"
                    # Trim the error string — these can be huge with full
                    # HTTP/SSE response bodies in OpenAI client errors.
                    err_str = str(e)
                    if len(err_str) > 500:
                        err_str = err_str[:497] + "..."
                    result["citation_error"] = err_str
                    citation = None

            # --- (2) Text chunks ---
            # Three sub-steps, surfaced individually so a UI can show
            # which one is currently the slow one (extraction is usually
            # ~1s, embedding can be 5-30s on CPU for a 50-page paper):
            #   2a. extract_text  — PyMuPDF + chunking
            #   2b. embed_chunks  — sentence-transformers, batched with
            #                        per-batch progress reporting
            #   2c. upsert_chunks — chromadb write
            _emit("extracting_text")
            text_chunks = extractor(pdf_path, description)
            if text_chunks:
                texts = [c["text"] for c in text_chunks]
                total_chunks = len(texts)
                _emit(
                    "embedding_chunks",
                    chunk_processed=0,
                    chunk_total=total_chunks,
                )

                def _on_batch(done: int, total: int) -> None:
                    _emit(
                        "embedding_chunks",
                        chunk_processed=done,
                        chunk_total=total,
                    )

                embeddings = self.embed_text_batched(
                    texts, batch_size=8, progress_cb=_on_batch,
                )
                ids = [f"{filename}_text_{j}" for j in range(len(text_chunks))]
                metadatas = [c["metadata"] for c in text_chunks]

                _emit(
                    "upserting_chunks",
                    chunk_processed=total_chunks,
                    chunk_total=total_chunks,
                )
                # A forced re-index replaces the file's text rows wholesale,
                # whatever id scheme wrote them. Done after the slow LLM and
                # embed steps so a failure there leaves the old rows intact.
                if self.force_reindex:
                    self._delete_source_rows(filename, citations=False)
                # Upsert so re-indexing replaces rather than errors.
                self.text_collection.upsert(
                    embeddings=embeddings,
                    documents=texts,
                    metadatas=metadatas,
                    ids=ids,
                )
                result["chunk_count"] = len(text_chunks)
                log.info(
                    "  ✓ Indexed %d text chunks for %s",
                    len(text_chunks), pdf_path.name,
                )
            elif self.force_reindex:
                # The file now yields no chunks: don't keep stale passages.
                self._delete_source_rows(filename, citations=False)

            # --- (3) Citation row upsert (after chunks succeeded) ---
            if citation:
                # Old citation rows go only when a new one is about to land.
                if self.force_reindex:
                    self._delete_source_rows(filename, texts=False)
                doc_text = self._citation_to_document(citation)
                doc_embedding = self.embed_text([doc_text])[0]
                meta = self._citation_to_metadata(citation, pdf_path.name)
                self.citation_collection.upsert(
                    embeddings=[doc_embedding],
                    documents=[doc_text],
                    metadatas=[meta],
                    ids=[cite_id],
                )
                result["citation"] = meta
                log.info(
                    "  ✓ Stored citation for %s: %s",
                    pdf_path.name,
                    citation.get("title", "(no title)"),
                )

            if not is_pdf:
                # Every non-PDF file gets a row in the citation collection, so
                # the explorer can show its kind and row count whether or not a
                # description could be written (abstract is empty then).
                citation = {"title": pdf_path.name, "abstract": description or ""}
                if self.force_reindex:
                    self._delete_source_rows(filename, texts=False)
                doc_text = self._citation_to_document(citation)
                meta = self._citation_to_metadata(citation, pdf_path.name)
                meta["record"] = DESCRIPTION_RECORDS[file_kind]
                meta["kind"] = file_kind
                if text_chunks and "row_count" in text_chunks[0]["metadata"]:
                    meta["row_count"] = text_chunks[0]["metadata"]["row_count"]
                self.citation_collection.upsert(
                    embeddings=[self.embed_text([doc_text])[0]],
                    documents=[doc_text],
                    metadatas=[meta],
                    ids=[cite_id],
                )
                result["citation"] = meta

            result["status"] = "indexed"
            _emit("done", status="indexed")
        except Exception as e:  # noqa: BLE001
            log.error("  ✗ Error processing %s: %s", pdf_path.name, e)
            result["error"] = str(e)
            result["status"] = "failed"
            _emit("done", status="failed", error=str(e))

        return result

    def _delete_source_rows(
        self, filename: str, *, texts: bool = True, citations: bool = True
    ) -> int:
        """Delete every row whose `source` is `filename` (text and/or citation)."""
        removed = 0
        colls = []
        if texts:
            colls.append(self.text_collection)
        if citations:
            colls.append(self.citation_collection)
        for coll in colls:
            found = coll.get(where={"source": filename})
            ids = (found or {}).get("ids") or []
            if ids:
                coll.delete(ids=ids)
                removed += len(ids)
        return removed

    def delete_source(self, filename: str) -> int:
        """
        Remove a file from the knowledge base: its chunks and citation row,
        then rebuild `bm25_corpus.json` (if one exists) from what remains so
        it no longer lists the deleted ids. Returns the number of rows removed.
        """
        removed = self._delete_source_rows(filename)
        if self._bm25_corpus_path.exists():
            # With nothing left, this removes the file.
            self.write_bm25_corpus()
        return removed

    def index_pdfs(self):
        """Process all PDFs: index text chunks + extract citation metadata."""
        if self.db_exists and not self.force_reindex:
            log.info("Database already exists at %s – skipping indexing.", self.db_path)
            log.info("  Text chunks : %d", self.text_collection.count())
            log.info("  Citations   : %d", self.citation_collection.count())
            log.info("Set force_reindex=True to rebuild.")
            # Defensive: an existing ChromaDB may pre-date the BM25
            # corpus file. Build it from the live chroma chunks so a
            # hybrid-retrieval-enabled MCP server picks it up at
            # startup without forcing a full re-index. Skipping when
            # the corpus file already exists keeps repeated
            # build_rag.py invocations cheap.
            self._build_bm25_corpus_if_missing()
            return

        if self.force_reindex and self.db_exists:
            log.info("Force reindex – clearing existing collections...")
            for name in ("text_chunks", "citations"):
                try:
                    self.client.delete_collection(name)
                except Exception:
                    pass
            self.text_collection = self.client.create_collection(
                name="text_chunks", metadata={"hnsw:space": "cosine"}
            )
            self.citation_collection = self.client.create_collection(
                name="citations", metadata={"hnsw:space": "cosine"}
            )
            # The old BM25 corpus is stale -- drop it so the post-index
            # build below produces a fresh file matching the new
            # ChromaDB contents.
            self._delete_bm25_corpus_if_present()

        pdf_files = sorted(
            p
            for p in self.pdf_folder.glob("**/*")
            if p.is_file() and p.suffix.lower() in FORMAT_EXTRACTORS
        )
        log.info("Found %d file(s) to process", len(pdf_files))

        for i, pdf_path in enumerate(pdf_files, 1):
            log.info("[%d/%d] %s", i, len(pdf_files), pdf_path.name)
            self.index_single_file(pdf_path)

        # Build the BM25 corpus file from the text_chunks we just
        # wrote to ChromaDB. 
        self.write_bm25_corpus()

        log.info("=" * 50)
        log.info("Indexing complete!")
        log.info("  Text chunks : %d", self.text_collection.count())
        log.info("  Citations   : %d", self.citation_collection.count())

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # BM25 corpus build (PALISADE G3 hybrid retrieval)
    # ------------------------------------------------------------------
    @property
    def _bm25_corpus_path(self) -> Path:
        """
        Filesystem location of the per-KB BM25 corpus file.

        """
        return Path(self.db_path) / BM25_CORPUS_FILENAME

    def _delete_bm25_corpus_if_present(self) -> None:
        """Drop the BM25 corpus file ahead of a force-reindex."""
        path = self._bm25_corpus_path
        if path.exists():
            try:
                path.unlink()
                log.info("BM25: removed stale corpus %s ahead of reindex", path)
            except OSError as exc:
                log.warning("BM25: could not remove %s: %s", path, exc)

    def _build_bm25_corpus_if_missing(self) -> None:
        """Skip the build when the corpus already exists on disk."""
        if self._bm25_corpus_path.exists():
            log.info(
                "BM25: corpus %s already present; skipping rebuild "
                "(delete the file or run with force_reindex to refresh)",
                self._bm25_corpus_path,
            )
            return
        self.write_bm25_corpus()

    def write_bm25_corpus(self) -> None:
        """
        Scan the ChromaDB `text_chunks` collection and write a BM25 corpus
        file alongside it (`write_bm25_corpus`). The backend calls it after
        each run of `index_single_pdf`, which writes none itself.
        """
        write_bm25_corpus(self.text_collection, self.db_path)

    def query(self, query: str, n_results: int = 5) -> Dict[str, Any]:
        """Search text chunks collection."""
        query_embedding = self.embed_text(
            [query], prompt=query_prompt(QUERY_INSTRUCTION)
        )[0]
        return self.text_collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
        )

    def query_citations(self, query: str, n_results: int = 5) -> Dict[str, Any]:
        """Search the citation metadata collection."""
        query_embedding = self.embed_text(
            [query], prompt=query_prompt(CITATION_QUERY_INSTRUCTION)
        )[0]
        return self.citation_collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
        )

    def get_citation_for_source(self, filename: str) -> Optional[dict]:
        """
        Look up the citation metadata for a specific PDF by filename.

        Returns the metadata dict (with all citation fields) or None.
        """
        results = self.citation_collection.get(
            where={"source": filename},
            include=["metadatas"],
        )
        if results["metadatas"]:
            meta = results["metadatas"][0]
            # Deserialise JSON-encoded list fields
            for field in ("authors", "keywords"):
                if meta.get(field):
                    try:
                        meta[field] = json.loads(meta[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            return meta
        return None

    def query_with_citations(
        self, query: str, n_results: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Semantic search over text chunks, then attach the citation metadata
        for each source document to the results.
        """
        raw = self.query(query, n_results=n_results)
        enriched: list[dict] = []
        seen_sources: dict[str, Optional[dict]] = {}

        for doc, meta in zip(raw["documents"][0], raw["metadatas"][0]):
            source = meta["source"]
            if source not in seen_sources:
                seen_sources[source] = self.get_citation_for_source(source)
            enriched.append({
                "text": doc,
                "chunk_metadata": meta,
                "citation": seen_sources[source],
            })
        return enriched


# ---------------------------------------------------------------------------
# CLI demo
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    rag = TextRAG(
        pdf_folder="./pdfs",
        db_path="./knowledge_bases/molten_salts_db",
        extract_citations=True,   # flip to False to skip LLM calls
        force_reindex=False,      # flip to True to rebuild
    )

    rag.index_pdfs()

    # --- Semantic search with citation metadata attached ---
    print("\n" + "=" * 50)
    print("Searching text chunks (with citations)...\n")
    results = rag.query_with_citations(
        "molten salt thermophysical properties", n_results=3
    )
    for i, r in enumerate(results, 1):
        print(f"{i}. {r['text'][:200]}...")
        print(f"   Source : {r['chunk_metadata']['source']}, "
              f"Page {r['chunk_metadata']['page']}")
        if r["citation"]:
            print(f"   Title  : {r['citation'].get('title', 'N/A')}")
            print(f"   Authors: {r['citation'].get('authors', 'N/A')}")
            print(f"   DOI    : {r['citation'].get('doi', 'N/A')}")
        print()

    # --- Search citations directly ---
    print("=" * 50)
    print("Searching citation collection...\n")
    cites = rag.query_citations("thermal conductivity fluoride salts", n_results=3)
    for i, (doc, meta) in enumerate(
        zip(cites["documents"][0], cites["metadatas"][0]), 1
    ):
        print(f"{i}. {meta.get('title', 'N/A')}")
        print(f"   Year: {meta.get('year', 'N/A')}  DOI: {meta.get('doi', 'N/A')}")
        print()
