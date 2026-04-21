"""arq job producer side.

Kept in a separate module from `worker.py` so the backend (producer)
imports a narrow surface and doesn't drag worker-only deps.

Pattern: lazy module-level `ArqRedis` pool, opened on first enqueue call
and closed from FastAPI's lifespan shutdown hook.
"""

from __future__ import annotations

import logging
from uuid import UUID

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings
from app.jobs.worker import QUEUE_NAME

logger = logging.getLogger(__name__)

_pool: ArqRedis | None = None


async def get_pool() -> ArqRedis:
    """Return the module-level singleton, opening it on first access."""
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


async def close_pool() -> None:
    """Close the pool. Idempotent."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def enqueue_process_document(document_id: UUID) -> None:
    """Enqueue a `process_document` job for the document id.

    UUID is passed as a string because arq's Redis serialization does not
    round-trip Python UUIDs cleanly across worker boundaries.
    """
    pool = await get_pool()
    job = await pool.enqueue_job(
        "process_document",
        str(document_id),
        _queue_name=QUEUE_NAME,
    )
    logger.info(
        "job_enqueued",
        extra={"document_id": str(document_id), "job_id": job.job_id if job else None},
    )
