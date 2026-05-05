"""Integration tests for the LLM-config admin API.

V1.5a tests run against an in-process FastAPI app via httpx
`ASGITransport`, NOT against the running backend container's port 8000.
The reason: `app.dependency_overrides` only takes effect on the same
process that owns the app instance. The /test endpoint (commit 5) needs
that override to swap in a stubbed `ModelFactory`. V1.5a doesn't
exercise the worker, so the in-process style is honest and gives us
cheap mocking. V1's worker-pipeline tests stay container-side because
they exercise an out-of-process worker; both styles coexist.

The `make_tenant` fixture from `tests/integration/conftest.py` still
talks to the real Postgres in the compose stack — the auto-detected
`/.dockerenv` switch picks the right hostname.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.db.models import TenantLLMConfig as TenantLLMConfigRow
from app.main import app

TENANT_HEADER = "X-Tenant-ID"


@pytest.fixture
async def asgi_client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_create_anthropic_config_returns_201_with_warning(
    asgi_client: AsyncClient,
    make_tenant: Callable[[], Awaitable[UUID]],
) -> None:
    tenant_id = await make_tenant()
    async with asgi_client as client:
        r = await client.post(
            "/api/v1/admin/llm-config",
            headers={TENANT_HEADER: str(tenant_id)},
            json={
                "provider": "anthropic",
                "model_name": "claude-sonnet-4-5",
                "api_key": "sk-ant-test-key-1234",
            },
        )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["provider"] == "anthropic"
    assert body["model_name"] == "claude-sonnet-4-5"
    assert body["api_key_last4"] == "1234"
    assert body["test_status"] == "untested"
    # Save bypassed a passing test → warning surfaces.
    assert body["warning"] is not None
    assert "without testing" in body["warning"].lower()


async def test_full_api_key_never_appears_in_response(
    asgi_client: AsyncClient,
    make_tenant: Callable[[], Awaitable[UUID]],
) -> None:
    tenant_id = await make_tenant()
    secret = "sk-ant-very-secret-token-9F4d"
    async with asgi_client as client:
        r = await client.post(
            "/api/v1/admin/llm-config",
            headers={TENANT_HEADER: str(tenant_id)},
            json={"provider": "anthropic", "model_name": "claude-3-5-sonnet", "api_key": secret},
        )
    assert r.status_code == 201
    assert secret not in r.text


async def test_get_returns_404_when_nothing_saved(
    asgi_client: AsyncClient,
    make_tenant: Callable[[], Awaitable[UUID]],
) -> None:
    tenant_id = await make_tenant()
    async with asgi_client as client:
        r = await client.get(
            "/api/v1/admin/llm-config",
            headers={TENANT_HEADER: str(tenant_id)},
        )
    assert r.status_code == 404


async def test_get_returns_last4_only(
    asgi_client: AsyncClient,
    make_tenant: Callable[[], Awaitable[UUID]],
) -> None:
    tenant_id = await make_tenant()
    secret = "sk-ant-12345678abcd"
    async with asgi_client as client:
        post = await client.post(
            "/api/v1/admin/llm-config",
            headers={TENANT_HEADER: str(tenant_id)},
            json={"provider": "anthropic", "model_name": "claude-3-5-sonnet", "api_key": secret},
        )
        assert post.status_code == 201
        get_resp = await client.get(
            "/api/v1/admin/llm-config",
            headers={TENANT_HEADER: str(tenant_id)},
        )
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["api_key_last4"] == "abcd"
    assert secret not in get_resp.text


async def test_update_with_empty_api_key_keeps_existing(
    asgi_client: AsyncClient,
    make_tenant: Callable[[], Awaitable[UUID]],
    host_engine: AsyncEngine,
) -> None:
    """Updating with an empty `api_key` field must NOT touch the encrypted
    blob or last4. Verified by reading the row directly.
    """
    tenant_id = await make_tenant()
    async with asgi_client as client:
        first = await client.post(
            "/api/v1/admin/llm-config",
            headers={TENANT_HEADER: str(tenant_id)},
            json={
                "provider": "anthropic",
                "model_name": "claude-3-5-sonnet",
                "api_key": "sk-ant-original-key-9999",
            },
        )
        assert first.status_code == 201

        sessionmaker = async_sessionmaker(host_engine, expire_on_commit=False)
        async with sessionmaker() as sess:
            row_before = (
                await sess.execute(
                    select(TenantLLMConfigRow).where(TenantLLMConfigRow.tenant_id == tenant_id)
                )
            ).scalar_one()
            blob_before = row_before.api_key_encrypted

        # Now update with `api_key` omitted — non-secret fields change,
        # the encrypted key stays put.
        second = await client.post(
            "/api/v1/admin/llm-config",
            headers={TENANT_HEADER: str(tenant_id)},
            json={"provider": "anthropic", "model_name": "claude-opus-4-7"},
        )
        assert second.status_code == 201
        assert second.json()["api_key_last4"] == "9999"
        assert second.json()["model_name"] == "claude-opus-4-7"

        async with sessionmaker() as sess:
            row_after = (
                await sess.execute(
                    select(TenantLLMConfigRow).where(TenantLLMConfigRow.tenant_id == tenant_id)
                )
            ).scalar_one()
            assert row_after.api_key_encrypted == blob_before


async def test_create_non_ollama_without_api_key_returns_400(
    asgi_client: AsyncClient,
    make_tenant: Callable[[], Awaitable[UUID]],
) -> None:
    tenant_id = await make_tenant()
    async with asgi_client as client:
        r = await client.post(
            "/api/v1/admin/llm-config",
            headers={TENANT_HEADER: str(tenant_id)},
            json={"provider": "anthropic", "model_name": "claude-sonnet-4-5"},
        )
    assert r.status_code == 400
    assert "api_key is required" in r.json()["detail"]


async def test_ollama_without_api_key_succeeds(
    asgi_client: AsyncClient,
    make_tenant: Callable[[], Awaitable[UUID]],
) -> None:
    tenant_id = await make_tenant()
    async with asgi_client as client:
        r = await client.post(
            "/api/v1/admin/llm-config",
            headers={TENANT_HEADER: str(tenant_id)},
            json={
                "provider": "ollama",
                "model_name": "qwen3",
                "base_url": "http://localhost:11434/v1",
            },
        )
    assert r.status_code == 201
    assert r.json()["api_key_last4"] == ""


async def test_openai_compatible_requires_base_url(
    asgi_client: AsyncClient,
    make_tenant: Callable[[], Awaitable[UUID]],
) -> None:
    tenant_id = await make_tenant()
    async with asgi_client as client:
        r = await client.post(
            "/api/v1/admin/llm-config",
            headers={TENANT_HEADER: str(tenant_id)},
            json={
                "provider": "openai_compatible",
                "model_name": "anymodel",
                "api_key": "sk-test-12345678",
            },
        )
    # Pydantic-level validation error → 422.
    assert r.status_code == 422
    assert "base_url" in r.text


async def test_delete_then_get_returns_404(
    asgi_client: AsyncClient,
    make_tenant: Callable[[], Awaitable[UUID]],
) -> None:
    tenant_id = await make_tenant()
    async with asgi_client as client:
        await client.post(
            "/api/v1/admin/llm-config",
            headers={TENANT_HEADER: str(tenant_id)},
            json={"provider": "anthropic", "model_name": "claude-3-5", "api_key": "sk-test-1234"},
        )
        d = await client.delete(
            "/api/v1/admin/llm-config",
            headers={TENANT_HEADER: str(tenant_id)},
        )
        assert d.status_code == 204

        g = await client.get(
            "/api/v1/admin/llm-config",
            headers={TENANT_HEADER: str(tenant_id)},
        )
    assert g.status_code == 404


async def test_get_without_tenant_header_returns_401(asgi_client: AsyncClient) -> None:
    async with asgi_client as client:
        r = await client.get("/api/v1/admin/llm-config")
    assert r.status_code == 401
