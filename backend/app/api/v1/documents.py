"""Document upload + lookup routes.

Upload order is deliberate: MIME sniff on the first 4 KB → content hash
over the full bytes → DB row insert (gets the doc id) → file move into
tenant storage under that id → enqueue. If the file move fails after
the row insert, we flip the row to status='failed' with a diagnostic
error_message so the state machine is never inconsistent.

Tenant directory creation is `mkdir(parents=True, exist_ok=True)`,
which is safe under concurrent uploads — `exist_ok` swallows the
`FileExistsError` that arrives on races.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
from pathlib import Path
from uuid import UUID

import magic
from fastapi import APIRouter, HTTPException, UploadFile, status
from sqlalchemy import select, update

from app.core.config import get_settings
from app.db.base import async_session_maker
from app.db.models import Document
from app.db.tenant_scope import tenant_scoped
from app.jobs.enqueue import enqueue_process_document
from app.models.documents import DocumentResponse
from app.tenancy.deps import CurrentTenant

logger = logging.getLogger(__name__)
router = APIRouter(tags=["documents"])

SNIFF_BYTES = 4096
READ_CHUNK = 1024 * 1024  # 1 MB
PDF_MIME = "application/pdf"


async def _mark_failed_after_save_error(document_id: UUID, reason: str) -> None:
    """Flip a freshly-inserted document to 'failed' if the file save couldn't
    complete. Uses a fresh session so the failing request's transaction has
    already been disposed of by the time we write.
    """
    async with async_session_maker() as sess, sess.begin():
        await sess.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(status="failed", error_message=f"upload_save_failed:{reason}"[:2000])
        )


@router.post(
    "/documents",
    status_code=status.HTTP_201_CREATED,
    response_model=DocumentResponse,
)
async def upload_document(
    file: UploadFile,
    tenant_id: CurrentTenant,
) -> DocumentResponse:
    """Upload a PDF for this tenant.

    Two-pass file read: peek the first 4 KB for real MIME detection, then
    rewind and stream-hash the full file. We never trust the filename
    extension or the client-provided content-type.
    """
    # --- 1. MIME sniff ---------------------------------------------------
    peek = await file.read(SNIFF_BYTES)
    detected = magic.from_buffer(peek, mime=True)
    if detected != PDF_MIME:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"expected {PDF_MIME}, got {detected}",
        )
    await file.seek(0)

    # --- 2. Stream to temp file, stream-hash full bytes ------------------
    hasher = hashlib.sha256()
    size = 0
    tmp_fd, tmp_path_str = tempfile.mkstemp(suffix=".pdf")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(tmp_fd, "wb") as out:
            while chunk := await file.read(READ_CHUNK):
                hasher.update(chunk)
                size += len(chunk)
                out.write(chunk)
        content_hash = hasher.hexdigest()

        # --- 3. Insert document row, get id ------------------------------
        doc = Document(
            tenant_id=tenant_id,
            filename=file.filename or "unnamed.pdf",
            mime_type=PDF_MIME,
            file_size_bytes=size,
            content_hash=content_hash,
            status="pending",
        )
        async with async_session_maker() as sess, sess.begin():
            sess.add(doc)
            await sess.flush()
            await sess.refresh(doc)
            doc_id = doc.id
            uploaded_at = doc.uploaded_at
            sess.expunge(doc)

        # --- 4. Move into tenant storage ---------------------------------
        settings = get_settings()
        tenant_dir = Path(settings.storage_dir) / "documents" / str(tenant_id)
        try:
            tenant_dir.mkdir(parents=True, exist_ok=True)
            target = tenant_dir / f"{doc_id}.pdf"
            shutil.move(str(tmp_path), str(target))
        except OSError as exc:
            logger.exception("upload_file_save_failed", extra={"document_id": str(doc_id)})
            await _mark_failed_after_save_error(doc_id, repr(exc))
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="failed to persist uploaded file",
            ) from exc
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                logger.warning("tmp_cleanup_failed", extra={"path": str(tmp_path)})

    # --- 5. Enqueue ------------------------------------------------------
    await enqueue_process_document(doc_id)
    logger.info("document_uploaded", extra={"document_id": str(doc_id), "size": size})

    return DocumentResponse(
        id=doc_id,
        status="pending",
        filename=doc.filename,
        uploaded_at=uploaded_at,
    )


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: UUID,
    tenant_id: CurrentTenant,
) -> DocumentResponse:
    """Return status + metadata for one document this tenant owns.

    Returns 404 for documents that don't exist AND for documents that
    belong to a different tenant. Never 403: we don't leak existence
    across tenants.
    """
    async with async_session_maker() as sess:
        stmt = tenant_scoped(
            select(Document).where(Document.id == document_id),
            tenant_id,
            Document,
        )
        doc = (await sess.execute(stmt)).scalar_one_or_none()

    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")

    return DocumentResponse(
        id=doc.id,
        status=doc.status,
        filename=doc.filename,
        uploaded_at=doc.uploaded_at,
        parsed_at=doc.parsed_at,
        error_message=doc.error_message,
        metadata=doc.metadata_,
    )
