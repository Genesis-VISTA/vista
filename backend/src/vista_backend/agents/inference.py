"""
Construction of the chat model and its inference provider.

`pydantic_ai.models.infer_model` defaults its `provider_factory` to
`infer_provider`, which builds every OpenAI-compatible provider from the
*process environment* — `OPENAI_BASE_URL` and `OPENAI_API_KEY`. Only
`.env.sample` sets those, so a VISTA started with no `.env` would resolve
`openai:claude-sonnet` against `api.openai.com`, where that model does not
exist, and fail with an authentication error that names the wrong host.

Every agent therefore builds its model through `build_inference_model` here,
which supplies the `provider_factory` hook so the endpoint and credential come
from `Settings` (and, on a single-user install, from the signed-in user's
settings row) rather than from whatever happens to be exported.

`resolve_inference_target` is the single place the precedence is decided:
the user's row first, `Settings` (which itself reads the environment) second.
It never raises, because an agent must still build without a credential --
listing a project's uploads or its MCP tools goes through the same pooled
agent as chat, and those must keep working on an install where nothing is
configured yet. `require_inference_credential` is the separate guard for the
paths that actually reach the model.
"""

import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic_ai.models import Model, infer_model, parse_model_id
from pydantic_ai.providers import Provider, infer_provider
from pydantic_ai.providers.openai import OpenAIProvider

from ..config import settings

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance
    from ..db.schemas import UserPublicWithConfig

# Provider names that resolve to *our* configured endpoint. The wider
# `OpenAIChatCompatibleProvider` family (deepseek, together, fireworks, ...)
# is deliberately excluded: each of those carries its own base URL and its own
# credential env var, so redirecting them at `openai_base_url` would silently
# point a user who asked for `deepseek:...` somewhere else.
_CONFIGURED_ENDPOINT_PROVIDERS = frozenset(
    {"openai", "openai-chat", "openai-responses"}
)


def build_provider_factory(
    api_key: str | None = None, base_url: str | None = None
) -> Callable[[str], Provider[Any]]:
    """
    A `provider_factory` for `infer_model`.

    For the OpenAI-compatible providers listed in
    `_CONFIGURED_ENDPOINT_PROVIDERS` this returns a provider pinned to an
    explicit endpoint and credential. Any other provider name falls through to
    pydantic-ai's own resolution, so `anthropic:...`, `google:...`,
    `ollama:...` and the rest keep working exactly as before.

    Args:
        api_key: Credential to use, taking precedence over
            `settings.openai_api_key`.
        base_url: Endpoint to use, taking precedence over
            `settings.openai_base_url`.
    """
    resolved_key = api_key or (
        settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
    )
    resolved_base_url = base_url or settings.openai_base_url

    def factory(provider_name: str) -> Provider[Any]:
        if provider_name in _CONFIGURED_ENDPOINT_PROVIDERS:
            # `base_url` is always explicit, so `OPENAI_BASE_URL` is never
            # consulted by the SDK. A `None` key is tolerated here rather than
            # raising: the services must start without one, and the missing
            # credential is reported when inference is actually attempted.
            return OpenAIProvider(base_url=resolved_base_url, api_key=resolved_key)
        return infer_provider(provider_name)

    return factory


def build_inference_model(
    model: str | Model | None = None,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
) -> Model:
    """
    Resolve `model` to a pydantic-ai `Model` against the configured endpoint.

    Args:
        model: A `provider:name` string, an already-constructed `Model` (passed
            through untouched), or `None` to use `settings.model`.
        api_key: Credential override, as for `build_provider_factory`.
        base_url: Endpoint override, as for `build_provider_factory`.
    """
    if isinstance(model, Model):
        return model
    return infer_model(
        model or settings.model,
        provider_factory=build_provider_factory(api_key, base_url),
    )


def build_model_for(user: "UserPublicWithConfig | None" = None) -> Model:
    """
    Build the model for `user`, applying the precedence in
    `resolve_inference_target`. The convenience wrapper agents use.
    """
    target = resolve_inference_target(user)
    return build_inference_model(
        target.model, api_key=target.api_key, base_url=target.base_url
    )


# Where a researcher goes to fix a missing credential. Named once so the
# guidance the API returns and the message a background caller logs cannot
# drift apart.
SETTINGS_LOCATION = (
    "Settings (click your name in the bottom-left corner) → “Inference API key”"
)


def rejected_credential_detail(model_name: str) -> str:
    """
    The message for a credential the provider refused (401/403).

    Same shape and same destination as `MissingInferenceCredential.detail`,
    because to a researcher the two are one problem: the key in the settings
    modal is not one the endpoint accepts.
    """
    return (
        f"The configured inference API key was rejected when calling "
        f"{model_name!r}. Check the key in {SETTINGS_LOCATION}, and that it "
        "belongs to the endpoint configured beside it."
    )


class MissingInferenceCredential(Exception):
    """
    No inference credential is configured, from any source.

    Carries the message shown to the researcher rather than a stack trace:
    on a fresh install this is the expected state, not a fault, and the only
    useful response names the setting and where to enter it.
    """

    def __init__(self, model: str, base_url: str) -> None:
        self.model = model
        self.base_url = base_url
        self.detail = (
            f"No inference API key is configured, so {model!r} at {base_url} "
            f"cannot be reached. Add one in {SETTINGS_LOCATION}. "
            "Everything that does not need the model — projects, skills, "
            "knowledge bases, uploads — works without it."
        )
        super().__init__(self.detail)


@dataclass(frozen=True)
class InferenceTarget:
    """The model, endpoint, and credential one agent will actually use."""

    model: str
    base_url: str
    api_key: str | None

    @property
    def uses_configured_endpoint(self) -> bool:
        """
        Whether this target resolves to the endpoint we supply a credential for.

        `test` (the pydantic-ai stub the test suite uses), a locally served
        `ollama:` model, and any provider with its own credential environment
        variable all reach the model without `inference_api_key`, so demanding
        one would refuse work that would have succeeded.
        """
        if self.model == "test":
            return False
        with warnings.catch_warnings():
            # A legacy bare model name warns here; `infer_model` warns about
            # the same string anyway, so don't say it twice.
            warnings.simplefilter("ignore", DeprecationWarning)
            provider_name, _ = parse_model_id(self.model)
        return provider_name in _CONFIGURED_ENDPOINT_PROVIDERS

    @property
    def has_credential(self) -> bool:
        return bool(self.api_key)


def resolve_inference_target(
    user: "UserPublicWithConfig | None" = None,
) -> InferenceTarget:
    """
    Decide which model, endpoint, and credential to use.

    Precedence is the user's settings row, then `Settings` (which reads the
    environment and `.env`). The row wins because on a single-user install it
    is the only surface a researcher can reach without editing files, so a
    value typed into the settings modal has to beat a stale exported one.

    Never raises: see the module docstring.
    """
    row_key = getattr(user, "inference_api_key", None) if user else None
    row_model = getattr(user, "inference_model", None) if user else None
    row_base_url = getattr(user, "inference_base_url", None) if user else None

    env_key = (
        settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
    )
    return InferenceTarget(
        model=row_model or settings.model,
        base_url=row_base_url or settings.openai_base_url,
        api_key=row_key or env_key,
    )


def citation_credentials(user=None):
    """
    The inference credential for citation extraction, as `build_rag` wants it.

    Citation extraction lives in `build_rag`, which resolves its LLM from the
    process environment. That cannot see a key entered in the settings modal --
    it is encrypted in the user's row, and this module is what resolves it --
    so a user-created knowledge base would index its text and silently get no
    titles, authors, or DOIs. Callers pass the result of this down to the
    indexer instead.

    `build_rag` is imported lazily: it lives at the repo root rather than in a
    package, pulls in sentence-transformers and chromadb, and is otherwise only
    loaded when indexing actually starts.
    """
    from ..utils.indexer import _get_text_rag_cls

    _get_text_rag_cls()  # puts the repo root on sys.path
    from build_rag import LLMCredentials  # type: ignore[import-not-found]

    target = resolve_inference_target(user)
    # `model` is a pydantic-ai model id (`provider:name`), but `build_rag` puts
    # its value straight into an OpenAI `model=` field, where the prefix is not
    # a valid model name. Split it the same way `infer_model` would.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        _, model_name = parse_model_id(target.model)
    return LLMCredentials(
        base_url=target.base_url, api_key=target.api_key, model=model_name
    )


def require_inference_credential(
    user: "UserPublicWithConfig | None" = None,
) -> InferenceTarget:
    """
    Resolve the target and refuse to continue without a credential.

    Call this on paths that are about to send a request to the model, before
    any response has begun -- an exception raised inside a streaming generator
    arrives after the status line, where it can only surface as a broken
    stream.
    """
    target = resolve_inference_target(user)
    if target.uses_configured_endpoint and not target.has_credential:
        raise MissingInferenceCredential(target.model, target.base_url)
    return target
