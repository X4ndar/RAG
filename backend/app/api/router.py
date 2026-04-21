"""Top-level API router aggregation.

Feature routers are mounted on `/api/v1`. Add new versions by exposing a
new sub-package (e.g. `app.api.v2`) and mounting it alongside without
breaking existing clients.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import documents as v1_documents
from app.api.v1 import search as v1_search

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(v1_documents.router)
api_router.include_router(v1_search.router)
