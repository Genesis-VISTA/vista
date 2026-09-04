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
"""

from collections.abc import Callable
from typing import Any

from pydantic_ai.models import Model, infer_model
from pydantic_ai.providers import Provider, infer_provider
from pydantic_ai.providers.openai import OpenAIProvider

from ..config import settings

# Provider names that resolve to *our* configured endpoint. The wider
# `OpenAIChatCompatibleProvider` family (deepseek, together, fireworks, ...)
# is deliberately excluded: each of those carries its own base URL and its own
# credential env var, so redirecting them at `openai_base_url` would silently
# point a user who asked for `deepseek:...` somewhere else.
_CONFIGURED_ENDPOINT_PROVIDERS = frozenset(
    {"openai", "openai-chat", "openai-responses"}
)


def build_provider_factory(
    api_key: str | None = None,
) -> Callable[[str], Provider[Any]]:
    """
    A `provider_factory` for `infer_model`.

    For the OpenAI-compatible providers listed in
    `_CONFIGURED_ENDPOINT_PROVIDERS` this returns a provider pinned to
    `settings.openai_base_url` with an explicit credential. Any other provider
    name falls through to pydantic-ai's own resolution, so `anthropic:...`,
    `google:...`, `ollama:...` and the rest keep working exactly as before.

    Args:
        api_key: Credential to use, taking precedence over
            `settings.openai_api_key`. Passed by callers that resolve a
            per-user credential.
    """
    resolved_key = api_key or (
        settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
    )

    def factory(provider_name: str) -> Provider[Any]:
        if provider_name in _CONFIGURED_ENDPOINT_PROVIDERS:
            # `base_url` is always explicit, so `OPENAI_BASE_URL` is never
            # consulted by the SDK. A `None` key is tolerated here rather than
            # raising: the services must start without one, and the missing
            # credential is reported when inference is actually attempted.
            return OpenAIProvider(
                base_url=settings.openai_base_url, api_key=resolved_key
            )
        return infer_provider(provider_name)

    return factory


def build_inference_model(
    model: str | Model | None = None, *, api_key: str | None = None
) -> Model:
    """
    Resolve `model` to a pydantic-ai `Model` against the configured endpoint.

    Args:
        model: A `provider:name` string, an already-constructed `Model` (passed
            through untouched), or `None` to use `settings.model`.
        api_key: Credential override, as for `build_provider_factory`.
    """
    if isinstance(model, Model):
        return model
    return infer_model(
        model or settings.model, provider_factory=build_provider_factory(api_key)
    )
