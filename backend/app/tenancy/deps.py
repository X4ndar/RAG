"""FastAPI dependencies for tenant resolution.

The existing `TenantMiddleware` attaches an optional `request.state.tenant_id`
for logging, but never rejects. This module is where enforcement lives: any
route that touches tenant data declares `tenant_id: UUID = Depends(
get_current_tenant)` and gets a 401 if the caller hasn't supplied a valid
`X-Tenant-ID`.

Keeping enforcement in a dependency means each route makes its intent
explicit in the signature; middleware alone makes the contract invisible.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status

TENANT_HEADER = "X-Tenant-ID"


async def get_current_tenant(request: Request) -> UUID:
    """Return the current tenant UUID, or raise 401.

    401 covers both the missing header and a malformed value. We
    deliberately use 401 (not 400 or 403): this is an auth-layer concern
    — pre-auth, the caller has not identified themselves.
    """
    raw = request.headers.get(TENANT_HEADER)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"{TENANT_HEADER} header is required",
        )
    try:
        return UUID(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"{TENANT_HEADER} must be a valid UUID",
        ) from exc


CurrentTenant = Annotated[UUID, Depends(get_current_tenant)]
"""Reusable dependency annotation. Routes write `tenant_id: CurrentTenant`
instead of `tenant_id: UUID = Depends(get_current_tenant)` so the function
signature stays free of runtime function calls.
"""
