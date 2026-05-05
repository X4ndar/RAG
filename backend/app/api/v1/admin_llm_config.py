"""Admin endpoints for LLM-provider configuration.

Three CRUD-shaped routes in V1.5a:

* POST /api/v1/admin/llm-config        — create or replace
* GET  /api/v1/admin/llm-config        — read (last-4 view)
* DELETE /api/v1/admin/llm-config      — wipe

The /test endpoint lands in commit 5; both ship under the same
APIRouter so the URL space stays one prefix.

Save semantics:

* New row + non-ollama provider + empty api_key  → 400.
* Existing row + empty api_key                    → keep the saved
  encrypted blob and last4 (non-secret fields are still updated).
* Any provider + non-empty api_key                → encrypt, update
  both api_key_encrypted and api_key_last4 atomically.

The 201 response carries a `warning` field when the saved row's
test_status is not 'passed'. The UI surfaces it as a banner.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic_ai import Agent
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from app.db.base import async_session_maker
from app.db.models import TenantLLMConfig as TenantLLMConfigRow
from app.db.tenant_scope import tenant_scoped
from app.llm.encryption import encrypt, last4
from app.llm.factory import (
    PROVIDER_TIMEOUTS_S,
    ModelFactory,
    TenantLLMConfig,
    get_model_factory,
)
from app.llm.sanitise import safe_provider_message
from app.models.llm_config import LLMConfigCreate, LLMConfigResponse, TestResult
from app.tenancy.deps import CurrentTenant

logger = logging.getLogger(__name__)
router = APIRouter(tags=["admin", "llm-config"])

WARNING_UNTESTED = (
    "Configuration saved without testing. Run a test before relying on it."
)
WARNING_FAILED = (
    "Configuration saved despite a failed test. The provider rejected the last attempt."
)


def _build_response(row: TenantLLMConfigRow, warning: str | None) -> LLMConfigResponse:
    return LLMConfigResponse.model_validate(
        {
            "id": row.id,
            "provider": row.provider,
            "model_name": row.model_name,
            "base_url": row.base_url,
            "api_key_last4": row.api_key_last4,
            "test_status": row.test_status,
            "test_error": row.test_error,
            "tested_at": row.tested_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "warning": warning,
        }
    )


def _warning_for(test_status: str) -> str | None:
    if test_status == "untested":
        return WARNING_UNTESTED
    if test_status == "failed":
        return WARNING_FAILED
    return None


@router.post(
    "/admin/llm-config",
    status_code=status.HTTP_201_CREATED,
    response_model=LLMConfigResponse,
)
async def upsert_llm_config(
    payload: LLMConfigCreate,
    tenant_id: CurrentTenant,
) -> LLMConfigResponse:
    """Create or replace this tenant's LLM provider configuration.

    V1.5a is one config per tenant (UNIQUE on tenant_id). The endpoint
    upserts: there's no separate PUT.
    """
    async with async_session_maker() as sess, sess.begin():
        existing = (
            await sess.execute(
                tenant_scoped(
                    select(TenantLLMConfigRow),
                    tenant_id,
                    TenantLLMConfigRow,
                )
            )
        ).scalar_one_or_none()

        new_api_key = payload.api_key.strip() if payload.api_key else ""

        if existing is None:
            if payload.provider != "ollama" and not new_api_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"api_key is required for provider {payload.provider!r}",
                )
            row = TenantLLMConfigRow(
                tenant_id=tenant_id,
                provider=payload.provider,
                model_name=payload.model_name,
                base_url=str(payload.base_url) if payload.base_url else None,
                api_key_encrypted=encrypt(new_api_key) if new_api_key else None,
                api_key_last4=last4(new_api_key),
                test_status="untested",
            )
            sess.add(row)
        else:
            row = existing
            row.provider = payload.provider
            row.model_name = payload.model_name
            row.base_url = str(payload.base_url) if payload.base_url else None
            if new_api_key:
                row.api_key_encrypted = encrypt(new_api_key)
                row.api_key_last4 = last4(new_api_key)
            elif payload.provider == "ollama":
                # Switching to ollama wipes any previously stored key
                # (ollama typically doesn't auth).
                row.api_key_encrypted = None
                row.api_key_last4 = ""
            elif row.api_key_encrypted is None:
                # Switching FROM ollama TO a non-ollama provider with an
                # empty key field is a 400 — the user has nothing on file.
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"api_key is required for provider {payload.provider!r}",
                )
            # Saving without running /test in this session resets the test
            # state. The form-level "did the user type any key during this
            # session" check (design note Save UX) belongs on the client.
            row.test_status = "untested"
            row.test_error = None
            row.tested_at = None

        await sess.flush()
        await sess.refresh(row)
        # Detach so attributes survive the session close below.
        sess.expunge(row)

    warning = _warning_for(row.test_status)
    logger.info(
        "llm_config_saved",
        extra={
            "tenant_id": str(tenant_id),
            "provider": row.provider,
            "test_status": row.test_status,
        },
    )
    return _build_response(row, warning)


@router.get(
    "/admin/llm-config",
    response_model=LLMConfigResponse,
)
async def get_llm_config(tenant_id: CurrentTenant) -> LLMConfigResponse:
    """Return the saved config (last-4 view). 404 if nothing saved."""
    async with async_session_maker() as sess:
        row = (
            await sess.execute(
                tenant_scoped(
                    select(TenantLLMConfigRow),
                    tenant_id,
                    TenantLLMConfigRow,
                )
            )
        ).scalar_one_or_none()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no LLM provider configured",
        )

    return _build_response(row, _warning_for(row.test_status))


@router.delete(
    "/admin/llm-config",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_llm_config(tenant_id: CurrentTenant) -> None:
    """Wipe this tenant's LLM provider configuration.

    Idempotent: no-op if there's nothing saved.
    """
    # `tenant_scoped` is typed for Select; sa.Delete needs the same WHERE
    # inline. The single tenant_id == ... clause is the only filter, so
    # there's no risk of cross-tenant deletion.
    async with async_session_maker() as sess, sess.begin():
        await sess.execute(
            sa_delete(TenantLLMConfigRow).where(
                TenantLLMConfigRow.tenant_id == tenant_id
            )
        )
    logger.info("llm_config_deleted", extra={"tenant_id": str(tenant_id)})


@router.post(
    "/admin/llm-config/test",
    response_model=TestResult,
)
async def test_connection(
    payload: LLMConfigCreate,
    tenant_id: CurrentTenant,
    model_factory: Annotated[ModelFactory, Depends(get_model_factory)],
) -> TestResult:
    """Run a one-shot prompt against the supplied provider config.

    Synchronous: the user clicks "Test", we wait up to PROVIDER_TIMEOUTS_S
    for the configured provider, return the result. Body matches the
    create payload so the user can test BEFORE saving.

    Production code uses the default `model_factory` returned by
    `get_model_factory`. Tests inject a stub via
    `app.dependency_overrides[get_model_factory]`. There is no env-var
    swap, no module-level monkey-patching of `build_model`, no other
    injection point.

    `safe_provider_message` is called on EVERY error path. The unit test
    `test_secret_hygiene_*` exercises each except branch with a fake
    API key embedded in the exception, asserting the key never reaches
    the response body or log line.
    """
    config = TenantLLMConfig(
        provider=payload.provider,
        model_name=payload.model_name,
        base_url=str(payload.base_url) if payload.base_url else None,
        api_key_plaintext=payload.api_key,
    )
    api_key = payload.api_key or ""
    timeout_s = PROVIDER_TIMEOUTS_S[payload.provider]

    started = time.perf_counter()
    try:
        model = model_factory(config)
        agent = Agent(model, retries=0)
        async with asyncio.timeout(timeout_s):
            result = await agent.run("Reply with the single word OK.")
    except TimeoutError:
        elapsed_ms = int(timeout_s * 1000)
        msg = f"timeout: provider did not respond within {timeout_s:.0f} s"
        logger.warning(
            "llm_config_test_timeout",
            extra={"tenant_id": str(tenant_id), "provider": payload.provider},
        )
        return TestResult(ok=False, error=msg, latency_ms=elapsed_ms)
    except (ConnectionError, OSError) as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        msg = f"unreachable: {safe_provider_message(exc, api_key)}"
        logger.warning(
            "llm_config_test_unreachable",
            extra={"tenant_id": str(tenant_id), "provider": payload.provider},
        )
        return TestResult(ok=False, error=msg, latency_ms=elapsed_ms)
    except Exception as exc:  # noqa: BLE001 — provider SDKs raise a wide variety
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        cleaned = safe_provider_message(exc, api_key)
        # Heuristic: provider SDKs commonly say "auth", "401", "403", "key" on
        # credential failures. Surface that to the UI as a more specific
        # category. Fallback message is the cleaned exception text.
        lowered = cleaned.lower()
        if any(token in lowered for token in ("auth", "401", "403", "permiss", "api key")):
            msg = f"authentication failed: {cleaned}"
        else:
            msg = cleaned
        logger.warning(
            "llm_config_test_failed",
            extra={
                "tenant_id": str(tenant_id),
                "provider": payload.provider,
                "exc_type": type(exc).__name__,
            },
        )
        return TestResult(ok=False, error=msg, latency_ms=elapsed_ms)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    output = result.output if hasattr(result, "output") else None
    ok = bool(output and str(output).strip())
    return TestResult(ok=ok, error=None if ok else "empty response", latency_ms=elapsed_ms)
