"""Top-level API router aggregation.

Feature routers (documents, chat, retrieval, tenants) will be registered here
as they arrive. Empty for v0.
"""

from __future__ import annotations

from fastapi import APIRouter

api_router = APIRouter(prefix="/api/v1")
