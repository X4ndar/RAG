"""Smoke test for the health endpoint."""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_tenant_header_attaches_to_request_state(client: AsyncClient) -> None:
    """Middleware must read X-Tenant-ID without failing the request."""
    response = await client.get("/health", headers={"X-Tenant-ID": "acme"})
    assert response.status_code == 200
