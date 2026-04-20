"""Qdrant integration for V1.

Design guardrail: the ONLY exported search function is
`search_for_tenant()`. It assembles the tenant-filter `Filter` internally,
so the tenant scope cannot be forgotten by API shape. No raw
`query_points` passthrough is exposed.

The collection name is a literal constant pinned to V1's schema
(BGE-M3 dense, 1024-dim, cosine). V2 creates a different collection and
backfills rather than mutating this one.

The `AsyncQdrantClient` is a lazy module-level singleton opened on first
access and closed on FastAPI shutdown. Routes and jobs share one pool,
not one client per request.
"""

from __future__ import annotations

import logging
from uuid import UUID

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    ScoredPoint,
    VectorParams,
)

from app.core.config import get_settings
from app.embeddings.bge_m3 import VECTOR_SIZE

logger = logging.getLogger(__name__)

COLLECTION_NAME = "documents_v1"

_client: AsyncQdrantClient | None = None


def get_client() -> AsyncQdrantClient:
    """Return the module-level singleton, opening it on first access.

    Kept out of import-time side effects so tests can monkeypatch the
    connection parameters before the first call.
    """
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
        )
    return _client


async def close_client() -> None:
    """Close the shared client. Called from FastAPI shutdown / arq shutdown."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def ensure_collection() -> None:
    """Create `documents_v1` with both payload indexes if missing.

    Idempotent: if the collection already exists this is a no-op. Safe to
    call on every startup. Called from the FastAPI lifespan hook and from
    arq's `on_startup`, not at import time.
    """
    client = get_client()

    if await client.collection_exists(COLLECTION_NAME):
        logger.info("qdrant_collection_exists", extra={"collection": COLLECTION_NAME})
        return

    logger.info(
        "qdrant_create_collection",
        extra={"collection": COLLECTION_NAME, "vector_size": VECTOR_SIZE},
    )
    await client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
    )
    await client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="tenant_id",
        field_schema=PayloadSchemaType.KEYWORD,
    )
    await client.create_payload_index(
        collection_name=COLLECTION_NAME,
        field_name="document_id",
        field_schema=PayloadSchemaType.KEYWORD,
    )


async def upsert_chunks(
    *,
    tenant_id: UUID,
    document_id: UUID,
    chunks: list[tuple[UUID, int, list[float]]],
) -> None:
    """Upsert one document's chunk vectors into `documents_v1`.

    Args:
        tenant_id: Owning tenant for all chunks in the batch.
        document_id: Document every chunk belongs to.
        chunks: Sequence of `(chunk_id, chunk_index, vector)` tuples.
            `chunk_id` must be the same UUID as `chunks.id` in Postgres
            so results can be hydrated with one `WHERE id = ANY(:ids)` query.
    """
    if not chunks:
        return
    client = get_client()
    points = [
        PointStruct(
            id=str(chunk_id),
            vector=vector,
            payload={
                "tenant_id": str(tenant_id),
                "document_id": str(document_id),
                "chunk_index": chunk_index,
            },
        )
        for (chunk_id, chunk_index, vector) in chunks
    ]
    await client.upsert(collection_name=COLLECTION_NAME, points=points)


async def search_for_tenant(
    tenant_id: UUID,
    query_vector: list[float],
    top_k: int,
) -> list[ScoredPoint]:
    """Search `documents_v1` with a mandatory tenant filter.

    This is the only search function exposed by the module. No raw
    `query_points` wrapper is exported, so the tenant filter cannot be
    omitted by calling-site mistake.

    Args:
        tenant_id: UUID of the requesting tenant.
        query_vector: Dense 1024-dim query embedding (BGE-M3).
        top_k: Maximum number of points to return.

    Returns:
        Scored points in descending relevance order.
    """
    client = get_client()
    response = await client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        query_filter=Filter(
            must=[
                FieldCondition(
                    key="tenant_id",
                    match=MatchValue(value=str(tenant_id)),
                ),
            ],
        ),
        limit=top_k,
    )
    return response.points
