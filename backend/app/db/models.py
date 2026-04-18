"""SQLAlchemy ORM models: tenants, documents, chunks.

Every tenant-owned table inherits from `TenantOwned`, which contributes the
`tenant_id` column and FK. Queries against these tables must go through
`tenant_scoped()` in `app.db.tenant_scope` so the WHERE filter cannot be
forgotten by accident.

JSONB shape conventions (not enforced by Postgres):

`Document.metadata_`:
    {
        "source": "upload",               # "upload" | future: "gdrive" | "email" | ...
        "original_filename": "contrat.pdf",
        "page_count": 12,
        "docling_version": "2.x.y",
        "language_hint": null,
    }

`Chunk.metadata_`:
    {
        "section_path": ["Chapitre 2", "Article 3"],
        "block_type": "paragraph",        # "paragraph" | "table" | "heading" | "list"
    }
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base

DOCUMENT_STATUSES = (
    "pending",
    "parsing",
    "chunking",
    "embedding",
    "ready",
    "failed",
)


class TenantOwned:
    """Mixin for tenant-scoped tables.

    Contributes a non-nullable `tenant_id` UUID column with a cascading FK to
    `tenants.id`. Subclasses must also register a composite index that begins
    with `tenant_id` for the query patterns they expect.
    """

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )


class Tenant(Base):
    """A single customer account. One tenant, many documents and chunks."""

    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Document(Base, TenantOwned):
    """An uploaded file. State machine: pending -> parsing -> chunking ->
    embedding -> ready | failed. Status transitions are driven by the worker.

    The `ix_documents_tenant_content_hash` index is a dedup surface for V1.5;
    V1 allows duplicate uploads to proceed (no 409 logic yet).
    """

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "file_size_bytes >= 0",
            name="documents_file_size_bytes_check",
        ),
        CheckConstraint(
            f"status IN ({', '.join(repr(s) for s in DOCUMENT_STATUSES)})",
            name="documents_status_check",
        ),
        Index("ix_documents_tenant_id_id", "tenant_id", "id"),
        Index("ix_documents_tenant_id_status", "tenant_id", "status"),
        Index("ix_documents_tenant_content_hash", "tenant_id", "content_hash"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="pending",
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embed_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    parsed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=sql_text("'{}'::jsonb"),
    )


class Chunk(Base, TenantOwned):
    """A retrieval unit produced by the chunker. Vectors themselves live in
    Qdrant; the `id` here is also the Qdrant point id.
    """

    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint("token_count > 0", name="chunks_token_count_check"),
        CheckConstraint("char_start >= 0", name="chunks_char_start_check"),
        CheckConstraint("char_end > char_start", name="chunks_char_end_check"),
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="chunks_document_chunk_index_key",
        ),
        Index("ix_chunks_tenant_document", "tenant_id", "document_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=sql_text("'{}'::jsonb"),
    )
