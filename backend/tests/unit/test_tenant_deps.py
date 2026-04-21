"""Unit tests for `get_current_tenant`.

Tested through a one-route FastAPI app + ASGI transport so the assertions
cover the full dependency-injection path, not just the helper's pure
Python behaviour.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.tenancy.deps import TENANT_HEADER, CurrentTenant


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = FastAPI()

    @app.get("/echo")
    async def echo(tenant_id: CurrentTenant) -> dict[str, str]:
        return {"tenant_id": str(tenant_id)}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_missing_header_returns_401(client: AsyncClient) -> None:
    r = await client.get("/echo")
    assert r.status_code == 401
    assert TENANT_HEADER in r.json()["detail"]


async def test_non_uuid_returns_401(client: AsyncClient) -> None:
    r = await client.get("/echo", headers={TENANT_HEADER: "not-a-uuid"})
    assert r.status_code == 401
    assert "valid UUID" in r.json()["detail"]


async def test_valid_uuid_reaches_handler(client: AsyncClient) -> None:
    tid = uuid4()
    r = await client.get("/echo", headers={TENANT_HEADER: str(tid)})
    assert r.status_code == 200
    assert r.json()["tenant_id"] == str(tid)


async def test_lowercase_header_still_works(client: AsyncClient) -> None:
    """Starlette header lookups are case-insensitive; a lower-case header
    must still satisfy the dependency.
    """
    tid = uuid4()
    r = await client.get("/echo", headers={TENANT_HEADER.lower(): str(tid)})
    assert r.status_code == 200
    assert r.json()["tenant_id"] == str(tid)
