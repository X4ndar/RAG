"""Unit tests for `app.llm.factory.build_model`.

build_model is contracted as a pure synchronous function: no I/O, no
network, no DB. The function under test should never reach a real
provider during these tests, and there are no mocks — we just inspect
the returned Pydantic AI Model object.

Coverage:
    * For each of the six provider keys, the right Pydantic AI Model
      subclass is returned.
    * Ollama and openai_compatible both route through OpenAIChatModel.
    * `api_key` and `base_url` propagate from TenantLLMConfig into the
      provider class on the model.
    * The factory is non-async (regression check on the design contract).
"""

from __future__ import annotations

import inspect

import pytest
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.mistral import MistralModel
from pydantic_ai.models.openai import OpenAIChatModel

from app.llm.factory import (
    PROVIDER_TIMEOUTS_S,
    LLMNotConfiguredError,
    ProviderType,
    TenantLLMConfig,
    build_model,
)


def _config(provider: ProviderType, **overrides: object) -> TenantLLMConfig:
    base = {
        "provider": provider,
        "model_name": "stub-model",
        "base_url": None,
        "api_key_plaintext": "fake-key-1234",
    }
    base.update(overrides)
    return TenantLLMConfig(**base)  # type: ignore[arg-type]


def test_build_model_is_synchronous() -> None:
    """The factory must be a plain sync function. Async dispatch would
    pull I/O into a place that the design says has none."""
    assert not inspect.iscoroutinefunction(build_model), (
        "build_model must be sync; dispatch has no I/O. "
        "Anything async means a network or DB call has crept in."
    )


@pytest.mark.parametrize(
    ("provider", "expected_cls"),
    [
        ("anthropic", AnthropicModel),
        ("openai", OpenAIChatModel),
        ("openai_compatible", OpenAIChatModel),
        ("ollama", OpenAIChatModel),
        ("google", GoogleModel),
        ("mistral", MistralModel),
    ],
)
def test_build_model_returns_expected_subclass(
    provider: ProviderType, expected_cls: type[Model]
) -> None:
    model = build_model(_config(provider))
    assert isinstance(model, expected_cls), (
        f"provider={provider!r} should return {expected_cls.__name__}, "
        f"got {type(model).__name__}"
    )


def test_openai_propagates_base_url() -> None:
    """The `base_url` from TenantLLMConfig must reach the OpenAI provider."""
    cfg = _config("openai", base_url="https://api.example.com/v1")
    model = build_model(cfg)
    # Pydantic AI's OpenAIChatModel wraps the user-supplied AsyncOpenAI client;
    # the underlying client's base_url is the propagation point.
    inner = model._provider.client.base_url  # type: ignore[attr-defined]
    assert "api.example.com" in str(inner), str(inner)


def test_ollama_routes_through_openai_with_base_url() -> None:
    cfg = _config("ollama", base_url="http://localhost:11434/v1", api_key_plaintext=None)
    model = build_model(cfg)
    assert isinstance(model, OpenAIChatModel)
    inner = model._provider.client.base_url  # type: ignore[attr-defined]
    assert "11434" in str(inner)


def test_openai_compatible_propagates_base_url() -> None:
    cfg = _config("openai_compatible", base_url="https://gateway.example/v1")
    model = build_model(cfg)
    assert isinstance(model, OpenAIChatModel)
    inner = model._provider.client.base_url  # type: ignore[attr-defined]
    assert "gateway.example" in str(inner)


def test_anthropic_no_base_url_field() -> None:
    """AnthropicProvider doesn't take base_url in our public form. The
    config's base_url is just ignored for this provider."""
    model = build_model(_config("anthropic", base_url="should-be-ignored"))
    assert isinstance(model, AnthropicModel)


def test_provider_timeouts_cover_every_provider() -> None:
    """If we add a provider, the timeout map must grow with it.
    Out-of-band invariant catching forgotten table entries."""
    expected = {"anthropic", "openai", "google", "mistral", "openai_compatible", "ollama"}
    assert set(PROVIDER_TIMEOUTS_S.keys()) == expected
    assert PROVIDER_TIMEOUTS_S["ollama"] == 5.0
    for provider in expected - {"ollama"}:
        assert PROVIDER_TIMEOUTS_S[provider] == 15.0, (
            f"cloud provider {provider} should have a 15 s budget; "
            "Mistral cold-starts can hit 5–8 s legitimately"
        )


def test_llm_not_configured_error_carries_tenant_id() -> None:
    from uuid import uuid4

    tid = uuid4()
    err = LLMNotConfiguredError(tid)
    assert err.tenant_id == tid
    assert str(tid) in str(err)
