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
    LargeBinary,
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

LLM_PROVIDER_TYPES = (
    "anthropic",
    "openai",
    "google",
    "mistral",
    "openai_compatible",
    "ollama",
)

LLM_TEST_STATUSES = ("untested", "passed", "failed")


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


class TenantLLMConfig(Base, TenantOwned):
    """A tenant's BYOM provider configuration.

    One row per tenant in V1.5a (UNIQUE on tenant_id). Multi-config (per-role)
    is a V2 change: drop the unique index and add a `role` column.

    `api_key_encrypted` is a Fernet token (bytes). It is NULL only for the
    `ollama` provider, which typically requires no auth. For every other
    provider the conditional CHECK guarantees both the encrypted blob (>= 80
    bytes, dodging Fernet truncation bugs) and the four-character last4 are
    present.

    `test_status` records the most recent /test outcome for this tenant's
    config — persisted so the admin UI can show it without rerunning.
    """

    __tablename__ = "tenant_llm_configs"
    __table_args__ = (
        CheckConstraint(
            f"provider IN ({', '.join(repr(p) for p in LLM_PROVIDER_TYPES)})",
            name="ck_tenant_llm_configs_provider",
        ),
        CheckConstraint(
            f"test_status IN ({', '.join(repr(s) for s in LLM_TEST_STATUSES)})",
            name="ck_tenant_llm_configs_test_status",
        ),
        CheckConstraint(
            "(provider = 'ollama')"
            " OR ("
            " api_key_encrypted IS NOT NULL"
            " AND length(api_key_encrypted) >= 80"
            " AND length(api_key_last4) = 4"
            ")",
            name="ck_tenant_llm_configs_api_key_present_when_required",
        ),
        Index(
            "ix_tenant_llm_configs_tenant",
            "tenant_id",
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    api_key_last4: Mapped[str] = mapped_column(
        String(4),
        nullable=False,
        server_default="",
    )
    test_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="untested",
    )
    test_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tested_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
