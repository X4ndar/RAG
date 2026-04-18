"""Tenant identification middleware (stub for v0).

Reads `X-Tenant-ID` from the request headers and stores it on `request.state`.
No enforcement, no session scoping, no RLS wiring yet. Those arrive when the
auth layer and first tenant-scoped tables land.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

TENANT_HEADER = "X-Tenant-ID"


class TenantMiddleware(BaseHTTPMiddleware):
    """Attach an optional tenant identifier to each request."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        tenant_id = request.headers.get(TENANT_HEADER)
        request.state.tenant_id = tenant_id
        return await call_next(request)
