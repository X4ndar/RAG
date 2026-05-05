"""FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.jobs.enqueue import close_pool
from app.llm.encryption import MasterKeyMissingError
from app.retrieval.qdrant_client import close_client, ensure_collection
from app.tenancy.middleware import TenantMiddleware

settings = get_settings()
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application startup / shutdown hooks.

    On startup: make sure the Qdrant collection and its payload indexes
    exist. Idempotent — re-running against an existing collection is a
    no-op.

    On shutdown: close the shared AsyncQdrantClient so the connection
    pool drains cleanly.
    """
    await ensure_collection()
    yield
    await close_client()
    await close_pool()


app = FastAPI(
    title="RAG SaaS",
    version="0.0.1",
    description="Multi-tenant RAG platform for French, Arabic, and English documents.",
    lifespan=lifespan,
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


@app.exception_handler(MasterKeyMissingError)
async def _master_key_missing_handler(
    _request: Request,
    _exc: MasterKeyMissingError,
) -> JSONResponse:
    """Map the LLM encryption module's `MasterKeyMissingError` to a single 503.

    The encryption module is the single chokepoint that raises this; this
    handler is the single place that maps it. Endpoints don't need to
    catch it themselves.
    """
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "LLM service is misconfigured. Contact your operator."},
    )


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    """Liveness probe. Returns 200 when the process is up."""
    return {"status": "ok"}
