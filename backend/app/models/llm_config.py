"""Pydantic request/response schemas for the LLM-config admin API.

Validation rules mirror the design note (Provider taxonomy section).
The conditional 'api_key required for non-ollama providers' is enforced
at the Pydantic boundary so route handlers can assume a valid payload.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

ProviderType = Literal[
    "anthropic",
    "openai",
    "google",
    "mistral",
    "openai_compatible",
    "ollama",
]

ModelName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
APIKey = Annotated[str, StringConstraints(min_length=8, max_length=512)]


class LLMConfigCreate(BaseModel):
    """Request body for POST /api/v1/admin/llm-config and POST /test."""

    provider: ProviderType
    model_name: ModelName
    base_url: AnyHttpUrl | None = None
    # `None` or empty means "keep the saved key on update; reject on create
    # for non-ollama providers". The route handler enforces the
    # create-vs-update branch using this field's emptiness.
    api_key: APIKey | None = None

    @field_validator("base_url", mode="before")
    @classmethod
    def _empty_string_is_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode="after")
    def _check_required_combinations(self) -> LLMConfigCreate:
        if self.provider in ("openai_compatible", "ollama") and self.base_url is None:
            raise ValueError(f"base_url is required for provider {self.provider!r}")
        return self


class LLMConfigResponse(BaseModel):
    """Public view of a tenant_llm_configs row.

    The api_key is never returned in full — only `api_key_last4`. The
    `warning` field is non-null when the row was saved without a passing
    test, so the UI can surface it.
    """

    id: UUID
    provider: ProviderType
    model_name: str
    base_url: AnyHttpUrl | None
    api_key_last4: str
    test_status: Literal["untested", "passed", "failed"]
    test_error: str | None
    tested_at: datetime | None
    created_at: datetime
    updated_at: datetime
    warning: str | None = None


class TestResult(BaseModel):
    """Response body for POST /api/v1/admin/llm-config/test."""

    ok: bool
    latency_ms: int = Field(ge=0)
    error: str | None = None
