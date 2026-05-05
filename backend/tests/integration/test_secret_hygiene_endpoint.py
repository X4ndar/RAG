"""Endpoint-level secret-hygiene tests.

Per the V1.5a design note's gate (g): the test-connection endpoint
must call `safe_provider_message` on EVERY except branch. The bug class
described in the note is "easy to add it for the generic Exception and
forget it for AuthenticationError or ConnectionError." Each test in
this file installs a stub `ModelFactory` whose model raises a specific
exception type with `FAKE_KEY` embedded in the message, then asserts
the key never appears in the response body.

Test transport: in-process FastAPI via `httpx.ASGITransport` so
`app.dependency_overrides[get_model_factory]` is reachable.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic_ai.models import Model

from app.llm.factory import TenantLLMConfig, get_model_factory
from app.main import app

FAKE_KEY = "sk-ant-FAKE-SECRET-KEY-9999"
TENANT_HEADER = "X-Tenant-ID"


@pytest.fixture
async def asgi_client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def cleanup_overrides() -> AsyncIterator[None]:
    yield
    app.dependency_overrides.pop(get_model_factory, None)


def _override_with_raising_model(exc: BaseException) -> None:
    class _RaisingModel(Model):  # type: ignore[misc]
        @property
        def model_name(self) -> str:
            return "stub"

        @property
        def system(self) -> str:
            return "stub"

        async def request(self, *args: object, **kwargs: object) -> object:
            raise exc

        async def request_stream(self, *args: object, **kwargs: object) -> object:
            raise exc

    def _factory(_config: TenantLLMConfig) -> Model:
        return _RaisingModel()

    app.dependency_overrides[get_model_factory] = lambda: _factory


async def _post_test(
    client: AsyncClient,
    tenant_id: UUID,
    *,
    provider: str = "anthropic",
    base_url: str | None = None,
    api_key: str | None = FAKE_KEY,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider": provider,
        "model_name": "stub",
    }
    if base_url is not None:
        payload["base_url"] = base_url
    if api_key is not None:
        payload["api_key"] = api_key
    r = await client.post(
        "/api/v1/admin/llm-config/test",
        headers={TENANT_HEADER: str(tenant_id)},
        json=payload,
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_authentication_error_branch_strips_key(
    asgi_client: AsyncClient,
    make_tenant: Callable[[], Awaitable[UUID]],
    cleanup_overrides: None,
) -> None:
    tenant_id = await make_tenant()
    _override_with_raising_model(RuntimeError(f"401 Unauthorized: api_key={FAKE_KEY}"))
    body = await _post_test(asgi_client, tenant_id)
    assert body["ok"] is False
    assert FAKE_KEY not in str(body)
    assert "authentication failed" in str(body["error"]).lower()


async def test_connection_error_branch_strips_key(
    asgi_client: AsyncClient,
    make_tenant: Callable[[], Awaitable[UUID]],
    cleanup_overrides: None,
) -> None:
    tenant_id = await make_tenant()
    _override_with_raising_model(
        ConnectionError(f"connect failed for Authorization: Bearer {FAKE_KEY}")
    )
    body = await _post_test(asgi_client, tenant_id)
    assert body["ok"] is False
    assert FAKE_KEY not in str(body)
    assert "unreachable" in str(body["error"]).lower()


async def test_oserror_branch_strips_key(
    asgi_client: AsyncClient,
    make_tenant: Callable[[], Awaitable[UUID]],
    cleanup_overrides: None,
) -> None:
    tenant_id = await make_tenant()
    _override_with_raising_model(OSError(f"[Errno 111] cannot reach api with api_key={FAKE_KEY}"))
    body = await _post_test(asgi_client, tenant_id)
    assert body["ok"] is False
    assert FAKE_KEY not in str(body)


async def test_generic_exception_branch_strips_key(
    asgi_client: AsyncClient,
    make_tenant: Callable[[], Awaitable[UUID]],
    cleanup_overrides: None,
) -> None:
    """Catch-all branch: unknown exception, no auth keyword in message."""
    tenant_id = await make_tenant()
    _override_with_raising_model(RuntimeError(f"weird internal error with token {FAKE_KEY}"))
    body = await _post_test(asgi_client, tenant_id)
    assert body["ok"] is False
    assert FAKE_KEY not in str(body)


async def test_timeout_branch_does_not_leak_key(
    asgi_client: AsyncClient,
    make_tenant: Callable[[], Awaitable[UUID]],
    cleanup_overrides: None,
) -> None:
    """Timeout produces a synthetic message that never names the key, but
    we still assert no leak in case of any logging side-effect."""

    class _SlowModel(Model):  # type: ignore[misc]
        @property
        def model_name(self) -> str:
            return "stub"

        @property
        def system(self) -> str:
            return "stub"

        async def request(self, *args: object, **kwargs: object) -> object:
            await asyncio.sleep(60)
            return None

        async def request_stream(self, *args: object, **kwargs: object) -> object:
            await asyncio.sleep(60)
            return None

    app.dependency_overrides[get_model_factory] = lambda: lambda _c: _SlowModel()

    tenant_id = await make_tenant()
    body = await _post_test(
        asgi_client,
        tenant_id,
        provider="ollama",  # 5 s budget vs 15 s, keeps the test fast
        base_url="http://localhost:11434/v1",
    )
    assert body["ok"] is False
    assert "timeout" in str(body["error"]).lower()
    assert FAKE_KEY not in str(body)


async def test_passing_test_returns_ok_true(
    asgi_client: AsyncClient,
    make_tenant: Callable[[], Awaitable[UUID]],
    cleanup_overrides: None,
) -> None:
    """Happy path: a TestModel returning 'OK' yields ok=true with no error."""
    from pydantic_ai.models.test import TestModel

    def _factory(_config: TenantLLMConfig) -> Model:
        return TestModel(custom_output_text="OK")

    app.dependency_overrides[get_model_factory] = lambda: _factory
    tenant_id = await make_tenant()
    body = await _post_test(asgi_client, tenant_id)
    assert body["ok"] is True
    assert body["error"] is None
    assert isinstance(body["latency_ms"], int)


def test_only_dependency_injection_can_swap_the_factory() -> None:
    """The ONLY way to substitute a different model factory is via FastAPI's
    `app.dependency_overrides`. No env-var swap, no module-level
    monkeypatch escape hatch in production code.
    """
    import inspect

    from app.api.v1 import admin_llm_config

    src = inspect.getsource(admin_llm_config)
    assert "Depends(get_model_factory)" in src
    assert "LLM_FORCE_TEST_MODEL" not in src
    assert "os.environ" not in src
