"""Provider factory: the single chokepoint that turns a tenant's BYOM
configuration into a Pydantic AI `Agent`.

V1.5a contracts (locked, do not drift):

* `build_model(config)` is a **pure synchronous function**. No I/O. No
  network. No DB. No `async`. The provider/model-class dispatch is a
  match on `config.provider`. This is what makes it unit-testable
  without mocks.

* The `Agent` returned by `agent_for_tenant()` has **no system prompt,
  no retries (retries=0), and the default `output_type` of `str`**. The
  V1.5b chat agent will set all three explicitly; V1.5a does not.

* Ollama is **routed through OpenAIChatModel** with the user's `base_url`
  pointed at Ollama's `/v1` endpoint. Ollama is OpenAI-compatible by
  contract, and current Pydantic AI ships no dedicated OllamaModel
  class; the OpenAI-compatible path is the canonical one for both
  `ollama` and `openai_compatible`.

* `get_model_factory()` is the FastAPI dependency tests override via
  `app.dependency_overrides`. Production code reads no env-var swap
  flag. For non-FastAPI call sites (e.g., the future worker), tests
  monkeypatch `app.llm.factory.build_model` directly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.mistral import MistralModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.mistral import MistralProvider
from pydantic_ai.providers.openai import OpenAIProvider
from sqlalchemy import select

from app.db.base import async_session_maker
from app.db.models import TenantLLMConfig as TenantLLMConfigRow
from app.llm.encryption import decrypt

ProviderType = Literal[
    "anthropic",
    "openai",
    "google",
    "mistral",
    "openai_compatible",
    "ollama",
]

# Per-provider connection-test timeouts. Cold starts vary widely:
# Mistral's hosted endpoint can legitimately take 5–8 s on a cold call;
# Anthropic and OpenAI usually <2 s; Ollama on localhost should be <1 s.
PROVIDER_TIMEOUTS_S: dict[ProviderType, float] = {
    "ollama": 5.0,
    "anthropic": 15.0,
    "openai": 15.0,
    "openai_compatible": 15.0,
    "google": 15.0,
    "mistral": 15.0,
}


@dataclass(frozen=True)
class TenantLLMConfig:
    """Decrypted, in-memory view of a tenant's provider configuration.

    The `api_key_plaintext` field's lifetime is the request handler that
    constructed it — never stored, never logged, never returned from any
    public endpoint. The Postgres row holds the encrypted form.
    """

    provider: ProviderType
    model_name: str
    base_url: str | None
    api_key_plaintext: str | None


class LLMNotConfiguredError(Exception):
    """Raised when a tenant has no row in `tenant_llm_configs`.

    The API layer maps this to 409 (`{detail: "no LLM provider
    configured for this tenant"}`). The exception itself carries no
    formatted message — the call site has the tenant id if it needs
    to log.
    """

    def __init__(self, tenant_id: UUID) -> None:
        super().__init__(f"no LLM provider configured for tenant {tenant_id}")
        self.tenant_id = tenant_id


ModelFactory = Callable[[TenantLLMConfig], Model]


def build_model(config: TenantLLMConfig) -> Model:
    """Pure-Python dispatch on `config.provider`. No I/O. No network.

    Returns a Pydantic AI `Model` ready to wrap in an `Agent`. The
    function is sync because dispatch is sync. Anything else would be a
    code smell — the actual provider call happens inside the Agent's
    runtime, not here.

    Ollama is routed through `OpenAIChatModel` because Ollama exposes
    an OpenAI-compatible `/v1` endpoint and Pydantic AI ships no
    dedicated `OllamaModel` class as of 1.90. The user-facing provider
    name in the form stays `ollama`; the routing is internal.
    """
    if config.provider == "anthropic":
        return AnthropicModel(
            config.model_name,
            provider=AnthropicProvider(api_key=config.api_key_plaintext or ""),
        )
    if config.provider in ("openai", "openai_compatible", "ollama"):
        return OpenAIChatModel(
            config.model_name,
            provider=OpenAIProvider(
                api_key=config.api_key_plaintext or "ollama",
                base_url=config.base_url,
            ),
        )
    if config.provider == "google":
        return GoogleModel(
            config.model_name,
            provider=GoogleProvider(api_key=config.api_key_plaintext or ""),
        )
    if config.provider == "mistral":
        return MistralModel(
            config.model_name,
            provider=MistralProvider(api_key=config.api_key_plaintext or ""),
        )
    # mypy/runtime safety: unreachable given the Literal type, but explicit.
    raise ValueError(f"unsupported provider: {config.provider!r}")  # pragma: no cover


def get_model_factory() -> ModelFactory:
    """FastAPI dependency. Tests override via `app.dependency_overrides[
    get_model_factory]` to inject a stub that returns Pydantic AI's
    `TestModel` (or raises a chosen exception).

    Production code reads NO env-var flag to swap factories. The
    dependency is the only injection point, and it's reachable only
    through FastAPI's DI graph — non-FastAPI callers (the future
    worker, etc.) import `build_model` directly and test via monkeypatch.
    """
    return build_model


async def load_tenant_llm_config(tenant_id: UUID) -> TenantLLMConfig | None:
    """Read the row for the tenant, decrypt the api_key, return a value
    object. None if the tenant has no row.

    Each call is a single `SELECT ... WHERE tenant_id = :t` (UNIQUE
    index, ~0.5 ms) plus one Fernet decrypt (~10 µs). No caching in
    V1.5a; profile-driven decision in V1.5b.
    """
    async with async_session_maker() as sess:
        stmt = select(TenantLLMConfigRow).where(TenantLLMConfigRow.tenant_id == tenant_id)
        row = (await sess.execute(stmt)).scalar_one_or_none()

    if row is None:
        return None

    api_key_plaintext = (
        None if row.api_key_encrypted is None else decrypt(row.api_key_encrypted)
    )

    return TenantLLMConfig(
        provider=row.provider,  # type: ignore[arg-type]  # CHECK constraint guarantees the value
        model_name=row.model_name,
        base_url=row.base_url,
        api_key_plaintext=api_key_plaintext,
    )


async def agent_for_tenant(tenant_id: UUID) -> Agent[None, str]:
    """Return a Pydantic AI Agent configured against the tenant's provider.

    Raises `LLMNotConfiguredError` if the tenant has no row. Raises
    `MasterKeyMissingError` (from `decrypt`) if the master key env var
    is unset; the FastAPI exception handler maps that to 503.

    V1.5a Agent shape: no system prompt, `retries=0`, default
    `output_type=str`. V1.5b's chat agent will configure all three.
    """
    config = await load_tenant_llm_config(tenant_id)
    if config is None:
        raise LLMNotConfiguredError(tenant_id)
    return Agent(build_model(config), retries=0)
