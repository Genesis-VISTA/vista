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
the provider the researcher chose, else the installation's own `Settings` when
they differ from the AmSC i2 preset, else i2 (see `PROVIDER_PRESETS`). It never
raises, because an agent must still build without a credential --
listing a project's uploads or its MCP tools goes through the same pooled
agent as chat, and those must keep working on an install where nothing is
configured yet. `require_inference_credential` is the separate guard for the
paths that actually reach the model.
"""

import warnings
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, infer_model, parse_model_id
from pydantic_ai.models.function import AgentInfo, FunctionModel
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

ProviderId = Literal["i2", "mag", "custom"]
TargetSource = Literal["user", "config", "default"]


@dataclass(frozen=True)
class ProviderPreset:
    """
    One inference provider the Settings modal offers.

    `base_url` is `None` only for Custom, whose endpoint is the researcher's
    own `inference_base_url`. `default_model` is a bare model name, used when
    the researcher has not chosen one; `None` means they must choose.
    """

    id: ProviderId
    name: str
    base_url: str | None
    default_model: str | None
    key_field: str
    """The `app_user` column holding this provider's API key."""

    @property
    def takes_url(self) -> bool:
        return self.base_url is None


# The one definition of the providers. The interface reads these through
# `GET /users/me/inference` rather than keeping its own copy, so a URL is
# never taken from the client and a default model changes in one place.
PROVIDER_PRESETS: dict[ProviderId, ProviderPreset] = {
    "i2": ProviderPreset(
        id="i2",
        name="AmSC i2",
        base_url="https://api.i2-core.american-science-cloud.org",
        default_model="claude-sonnet",
        # The key saved before provider choice existed, which was almost
        # always for i2.
        key_field="inference_api_key",
    ),
    "mag": ProviderPreset(
        id="mag",
        name="AmSC MAG",
        base_url="https://i2-api.staging.american-science-cloud.org/v1",
        default_model=None,
        key_field="inference_mag_api_key",
    ),
    "custom": ProviderPreset(
        id="custom",
        name="Custom",
        base_url=None,
        default_model=None,
        key_field="inference_custom_api_key",
    ),
}


def _qualify(model: str) -> str:
    """A preset's bare default as the `openai:` id every agent builds from."""
    return f"openai:{model}"


def _display_model_name(model: str) -> str:
    """
    Strip the `openai:` prefix for a message a researcher reads.

    `openai:` is an internal routing detail -- the Settings Model field and
    the model picker both write and show bare names, since it's the only
    provider surfaced through the UI today -- so echoing it back in an error
    banner would name something nobody typed.
    """
    return model.removeprefix("openai:")


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
    if target.model is None:
        return _no_model(target)
    return build_inference_model(
        target.model, api_key=target.api_key, base_url=target.base_url
    )


def _no_model(target: "InferenceTarget") -> Model:
    """
    The model for a provider with no model chosen and no default.

    Agents are built before anything reaches the model -- listing uploads or
    MCP tools goes through the same pooled agent -- so building must not
    fail. The paths that do reach the model check first, through
    `require_inference_credential`; this raises the same named condition for
    any that do not.
    """

    def refuse(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        raise MissingInferenceModel(target.provider)

    return FunctionModel(refuse, model_name="no-model")


# Where a researcher goes to fix a missing credential. Named once so the
# guidance the API returns and the message a background caller logs cannot
# drift apart.
SETTINGS_LOCATION = (
    "Settings (click your name in the bottom-left corner) → “Inference API key”"
)


def rejected_credential_detail(model_name: str | None) -> str:
    """
    The message for a credential the provider refused (401/403).

    Same shape and same destination as `MissingInferenceCredential.detail`,
    because to a researcher the two are one problem: the key in the settings
    modal is not one the endpoint accepts. `model_name` is `None` when the
    refusal came from listing a provider's models before one was chosen.
    """
    calling = (
        f"calling {_display_model_name(model_name)!r}"
        if model_name
        else "listing the provider's models"
    )
    return (
        f"The configured inference API key was rejected when {calling}. "
        f"Check the key in "
        f"{SETTINGS_LOCATION}, and that it belongs to the endpoint configured "
        "beside it."
    )


class MissingInferenceCredential(Exception):
    """
    No inference credential is configured, from any source.

    Carries the message shown to the researcher rather than a stack trace:
    on a fresh install this is the expected state, not a fault, and the only
    useful response names the setting and where to enter it.
    """

    def __init__(self, model: str | None, base_url: str) -> None:
        self.model = model
        self.base_url = base_url
        what = f"{_display_model_name(model)!r} at {base_url}" if model else base_url
        self.detail = (
            f"No inference API key is configured, so {what} "
            f"cannot be reached. Add one in {SETTINGS_LOCATION}. "
            "Everything that does not need the model — projects, skills, "
            "knowledge bases, uploads — works without it."
        )
        super().__init__(self.detail)


class MissingInferenceModel(Exception):
    """
    The chosen provider has no default model and the researcher chose none.

    Like `MissingInferenceCredential`, an expected state rather than a fault:
    after switching to AmSC MAG or Custom, the next step is to pick a model.
    The interface holds a send in this state; this is the backstop behind it.
    """

    def __init__(self, provider: ProviderId) -> None:
        self.provider = provider
        name = PROVIDER_PRESETS[provider].name
        self.detail = (
            f"No model is chosen for {name}, which has no default. Choose one "
            "from the model picker at the top of the chat."
        )
        super().__init__(self.detail)


@dataclass(frozen=True)
class InferenceTarget:
    """The model, endpoint, and credential one agent will actually use."""

    model: str | None
    """`None` when the provider has no default and none was chosen."""
    base_url: str
    api_key: str | None
    provider: ProviderId = "i2"
    source: TargetSource = "default"
    """
    Where the provider came from: the researcher's choice (`user`), the
    installation's own `Settings` (`config`), or the i2 preset (`default`).
    """
    model_is_default: bool = False
    """Whether `model` is the provider's default rather than a chosen one."""

    @property
    def uses_configured_endpoint(self) -> bool:
        """
        Whether this target resolves to the endpoint we supply a credential for.

        `test` (the pydantic-ai stub the test suite uses), a locally served
        `ollama:` model, and any provider with its own credential environment
        variable all reach the model without `inference_api_key`, so demanding
        one would refuse work that would have succeeded.

        With no model at all, the target is still the provider's endpoint: it
        is where a model will be chosen from.
        """
        if self.model is None:
            return True
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


def _differs_from_i2() -> bool:
    """Whether `Settings` names a target other than the i2 preset's."""
    i2 = PROVIDER_PRESETS["i2"]
    assert i2.base_url is not None and i2.default_model is not None
    return (
        settings.openai_base_url.rstrip("/") != i2.base_url.rstrip("/")
        or settings.model.removeprefix("openai:") != i2.default_model
    )


def resolve_inference_target(
    user: "UserPublicWithConfig | None" = None,
) -> InferenceTarget:
    """
    Decide which model, endpoint, and credential to use.

    1. The row names a provider: that preset's URL (Custom: the row's
       `inference_base_url`), that provider's key, and the row's model, else
       the preset's default -- possibly none. i2's key falls back to
       `Settings.openai_api_key`.
    2. No provider, and `Settings` differs from the i2 preset: `Settings`, as
       before providers existed, reported as Custom from configuration. The
       row's model and key still win over it, as they always have; its
       `inference_base_url` is Custom's endpoint and is not used here.
    3. Otherwise i2, with the row's `inference_api_key` (the key saved before
       providers existed), else `Settings.openai_api_key`.

    The row wins where it speaks because on a single-user install it is the
    only surface a researcher can reach without editing files.

    Never raises: see the module docstring.
    """

    def row(field: str) -> str | None:
        return getattr(user, field, None) if user else None

    env_key = (
        settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
    )
    row_model = row("inference_model")
    provider = row("inference_provider")

    if provider in PROVIDER_PRESETS:
        preset = PROVIDER_PRESETS[provider]  # type: ignore[index]
        default = _qualify(preset.default_model) if preset.default_model else None
        key = row(preset.key_field)
        if preset.id == "i2":
            key = key or env_key
        return InferenceTarget(
            model=row_model or default,
            base_url=preset.base_url or row("inference_base_url") or "",
            api_key=key,
            provider=preset.id,
            source="user",
            model_is_default=row_model is None and default is not None,
        )

    if _differs_from_i2():
        return InferenceTarget(
            model=row_model or settings.model,
            base_url=settings.openai_base_url,
            api_key=row("inference_api_key") or env_key,
            provider="custom",
            source="config",
            model_is_default=row_model is None,
        )

    i2 = PROVIDER_PRESETS["i2"]
    assert i2.base_url is not None and i2.default_model is not None
    return InferenceTarget(
        model=row_model or _qualify(i2.default_model),
        base_url=i2.base_url,
        api_key=row("inference_api_key") or env_key,
        provider="i2",
        source="default",
        model_is_default=row_model is None,
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
    if target.model is None:
        # Extraction would have to guess a model the provider may not serve.
        # `disabled` stops the indexer falling back to the environment's
        # credential, so extraction is reported off, as with no key at all.
        return LLMCredentials(disabled=True)
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
    Resolve the target and refuse to continue without a credential or a model.

    Call this on paths that are about to send a request to the model, before
    any response has begun -- an exception raised inside a streaming generator
    arrives after the status line, where it can only surface as a broken
    stream.
    """
    target = resolve_inference_target(user)
    if target.uses_configured_endpoint and not target.has_credential:
        raise MissingInferenceCredential(target.model, target.base_url)
    if target.model is None:
        raise MissingInferenceModel(target.provider)
    return target
