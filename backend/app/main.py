"""FastAPI application entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.tenancy.middleware import TenantMiddleware

settings = get_settings()
configure_logging(settings.log_level)

app = FastAPI(
    title="RAG SaaS",
    version="0.0.1",
    description="Multi-tenant RAG platform for French, Arabic, and English documents.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(TenantMiddleware)
app.include_router(api_router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    """Liveness probe. Returns 200 when the process is up."""
    return {"status": "ok"}
