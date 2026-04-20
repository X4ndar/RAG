"""arq worker: parse → chunk → embed → upsert pipeline for uploaded PDFs.

The job is idempotent: each run takes a row-level lock on the document,
checks status, and exits early if another worker already owns it or the
document is already past the failure/pending states. The lock is held
only while flipping status from 'pending' (or 'failed', for manual
retries) to 'parsing', then released BEFORE any heavy work runs.

State machine:
    pending | failed  ─(lock)─▶  parsing  ─▶  chunking  ─▶  embedding  ─▶  ready
                                    │            │             │
                                    └── exception ┴── exception ┘
                                                 │
                                                 ▼
                                              failed  (written in a fresh session)

Durations reported on the row are tight: `parse_duration_ms` brackets only
the Docling call; `embed_duration_ms` is summed over the batched `embed()`
calls alone. Orchestration overhead is not counted.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from arq.connections import RedisSettings
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.base import async_session_maker
from app.db.models import Chunk, Document
from app.embeddings.bge_m3 import embed, warm_up
from app.parsers.chunker import ChunkSpec, chunk_markdown
from app.parsers.pdf_docling import EmptyParseError, parse_pdf, warm_up_converter
from app.retrieval.qdrant_client import (
    close_client,
    ensure_collection,
    upsert_chunks,
)

# arq imports this module directly; `app.main` (which normally configures
# logging) never runs in the worker process. Wire up our log handler here
# so the pipeline's status transitions actually show up in `docker compose
# logs worker`.
configure_logging(get_settings().log_level)
logger = logging.getLogger(__name__)

QUEUE_NAME = "rag:docs"
EMBED_BATCH_SIZE = 32
ERROR_MESSAGE_LIMIT = 2000


def _storage_path(tenant_id: UUID, document_id: UUID) -> Path:
    settings = get_settings()
    return Path(settings.storage_dir) / "documents" / str(tenant_id) / f"{document_id}.pdf"


async def _acquire_and_claim(document_id: UUID) -> Document | None:
    """Transition the document to `parsing` atomically or bail out.

    Uses `SELECT ... FOR UPDATE SKIP LOCKED` so concurrent workers fanning
    out over the same queue don't pile up on the same row. The lock is
    held only inside this transaction; the outer pipeline runs without it.

    Returns the Document (now at status='parsing') or None if another
    worker owns it, the document is already processed, or it doesn't exist.
    """
    async with async_session_maker() as sess, sess.begin():
        stmt = (
            select(Document)
            .where(Document.id == document_id)
            .with_for_update(skip_locked=True)
        )
        doc = (await sess.execute(stmt)).scalar_one_or_none()

        if doc is None:
            logger.warning(
                "document_not_found_or_locked",
                extra={"document_id": str(document_id)},
            )
            return None

        if doc.status not in ("pending", "failed"):
            logger.info(
                "document_already_processing_or_done",
                extra={"document_id": str(document_id), "status": doc.status},
            )
            return None

        doc.status = "parsing"
        doc.error_message = None
        # committed at end of `sess.begin()` context; lock released here.
        await sess.flush()
        await sess.refresh(doc)
        sess.expunge(doc)
        return doc


async def _update_status(document_id: UUID, **values: Any) -> None:
    """Small-transaction helper for mid-pipeline status flips."""
    async with async_session_maker() as sess, sess.begin():
        await sess.execute(
            update(Document).where(Document.id == document_id).values(**values)
        )


async def _mark_failed(document_id: UUID, error: str) -> None:
    """Write status='failed' + error_message in a FRESH session.

    The pipeline's own session has already rolled back. Opening a new
    session guarantees we can persist the failure rather than leaving a
    zombie 'parsing'/'chunking'/'embedding' row on crash.
    """
    trimmed = error[:ERROR_MESSAGE_LIMIT]
    async with async_session_maker() as sess, sess.begin():
        await sess.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(status="failed", error_message=trimmed)
        )


async def _insert_chunks(
    *,
    document_id: UUID,
    tenant_id: UUID,
    specs: list[ChunkSpec],
) -> list[Chunk]:
    """Insert chunk rows with pre-generated UUIDs and return them.

    Each chunk's `id` is generated in Python (not deferred to the DB
    default) so the same UUID can be used as the Qdrant point id without
    a post-insert fetch round trip. Commit 6's search hydration relies
    on this identity.
    """
    rows = [
        Chunk(
            id=uuid4(),
            document_id=document_id,
            tenant_id=tenant_id,
            chunk_index=index,
            text=spec.text,
            token_count=spec.token_count,
            char_start=spec.char_start,
            char_end=spec.char_end,
        )
        for index, spec in enumerate(specs)
    ]
    async with async_session_maker() as sess, sess.begin():
        sess.add_all(rows)
        await sess.flush()
        for row in rows:
            await sess.refresh(row)
            sess.expunge(row)
    return rows


async def _embed_and_upsert(
    *,
    tenant_id: UUID,
    document_id: UUID,
    chunks: list[Chunk],
) -> int:
    """Batch-embed chunks and upsert to Qdrant. Returns total embed duration
    in milliseconds, measured strictly around the embed() calls.
    """
    embed_ms_total = 0
    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[start : start + EMBED_BATCH_SIZE]
        texts = [c.text for c in batch]

        t0 = time.perf_counter()
        vectors = await embed(texts)
        embed_ms_total += int((time.perf_counter() - t0) * 1000)

        await upsert_chunks(
            tenant_id=tenant_id,
            document_id=document_id,
            chunks=[(c.id, c.chunk_index, v) for c, v in zip(batch, vectors, strict=True)],
        )
    return embed_ms_total


async def process_document(ctx: dict[str, Any], document_id: str) -> None:
    """Parse, chunk, embed, and index a single uploaded PDF.

    Arg is a string (arq serialises UUIDs awkwardly across Redis); we
    parse back to UUID here.
    """
    doc_uuid = UUID(document_id)
    log_extra = {"document_id": document_id}

    doc = await _acquire_and_claim(doc_uuid)
    if doc is None:
        return

    tenant_id = doc.tenant_id
    log_extra["tenant_id"] = str(tenant_id)

    try:
        pdf_path = _storage_path(tenant_id, doc_uuid)
        if not pdf_path.exists():
            raise FileNotFoundError(f"uploaded PDF missing at {pdf_path}")

        # --- parse ------------------------------------------------------
        t0 = time.perf_counter()
        parsed = await parse_pdf(pdf_path)
        parse_ms = int((time.perf_counter() - t0) * 1000)
        logger.info("parsed", extra={**log_extra, "parse_ms": parse_ms, "pages": parsed.page_count})

        from datetime import UTC, datetime

        await _update_status(
            doc_uuid,
            status="chunking",
            parsed_at=datetime.now(UTC),
            parse_duration_ms=parse_ms,
            metadata_={
                "source": "upload",
                "original_filename": doc.filename,
                "page_count": parsed.page_count,
                "docling_version": parsed.docling_version,
                "language_hint": None,
            },
        )

        # --- chunk ------------------------------------------------------
        specs = chunk_markdown(parsed.markdown)
        if not specs:
            raise EmptyParseError("no_chunks_produced")
        logger.info("chunked", extra={**log_extra, "chunks": len(specs)})

        chunks = await _insert_chunks(
            document_id=doc_uuid, tenant_id=tenant_id, specs=specs
        )
        await _update_status(doc_uuid, status="embedding")

        # --- embed + upsert --------------------------------------------
        embed_ms = await _embed_and_upsert(
            tenant_id=tenant_id, document_id=doc_uuid, chunks=chunks
        )
        logger.info("embedded", extra={**log_extra, "embed_ms": embed_ms})

        await _update_status(
            doc_uuid, status="ready", embed_duration_ms=embed_ms
        )
        logger.info("ready", extra=log_extra)

    except Exception as exc:  # noqa: BLE001
        logger.exception("pipeline_failed", extra=log_extra)
        await _mark_failed(doc_uuid, repr(exc))
        raise


# ---------- arq plumbing -----------------------------------------------


async def startup(ctx: dict[str, Any]) -> None:
    """Pre-load the heavy models before we accept jobs.

    Warming these here means the first job doesn't pay the model-load
    cost on its own timer. ensure_collection is called so the worker
    can still start a pipeline if FastAPI hasn't booted first.
    """
    logger.info("worker_starting")
    await warm_up()
    await warm_up_converter()
    await ensure_collection()
    logger.info("worker_ready")


async def shutdown(ctx: dict[str, Any]) -> None:
    logger.info("worker_shutting_down")
    await close_client()


class WorkerSettings:
    """arq entrypoint. `arq app.jobs.worker.WorkerSettings` in the compose
    command launches a worker with these settings.
    """

    functions = (process_document,)
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    queue_name = QUEUE_NAME
    max_jobs = 2
    # TODO: job_timeout=300s is conservative for small test PDFs. Raise once
    # real-world documents (OCR paths, 50+ page contracts) miss the deadline.
    job_timeout = 300
    max_tries = 1
    keep_result = 3600
    on_startup = startup
    on_shutdown = shutdown
