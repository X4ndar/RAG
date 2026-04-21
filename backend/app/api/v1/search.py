"""Search endpoint.

Hydration uses a single `WHERE id = ANY(:ids) AND tenant_id = :t` query
— defense in depth on top of the Qdrant payload filter, and no N+1
loops. Postgres doesn't preserve the query order of `ANY`, so we
re-sort the hydrated rows in Python by the Qdrant scored-point order
before returning.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.db.base import async_session_maker
from app.db.models import Chunk
from app.db.tenant_scope import tenant_scoped
from app.embeddings.bge_m3 import embed_query
from app.models.search import SearchHit, SearchResponse
from app.retrieval.qdrant_client import search_for_tenant
from app.tenancy.deps import CurrentTenant

logger = logging.getLogger(__name__)
router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse)
async def search(
    q: Annotated[str, Query(min_length=1, max_length=500)],
    tenant_id: CurrentTenant,
    top_k: Annotated[int, Query(ge=1, le=50)] = 10,
) -> SearchResponse:
    """Dense retrieval over `documents_v1` scoped to the caller's tenant."""
    query_vector = await embed_query(q)
    points = await search_for_tenant(tenant_id, query_vector, top_k)
    if not points:
        return SearchResponse(results=[])

    ids = [UUID(str(p.id)) for p in points]
    async with async_session_maker() as sess:
        stmt = tenant_scoped(
            select(Chunk).where(Chunk.id.in_(ids)),
            tenant_id,
            Chunk,
        )
        rows = (await sess.execute(stmt)).scalars().all()

    by_id = {chunk.id: chunk for chunk in rows}
    ordered: list[SearchHit] = []
    for point in points:
        chunk = by_id.get(UUID(str(point.id)))
        if chunk is None:
            # Qdrant has a point whose corresponding chunk row is missing
            # from this tenant's slice. Either the upsert payload was wrong
            # (and the Qdrant tenant filter leaked), or a retention job ran
            # between the vector search and the hydration. Skip defensively.
            logger.warning(
                "search_hit_missing_chunk_row",
                extra={"point_id": str(point.id), "tenant_id": str(tenant_id)},
            )
            continue
        ordered.append(
            SearchHit(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                chunk_index=chunk.chunk_index,
                text=chunk.text,
                score=point.score,
                page_number=chunk.page_number,
            )
        )

    return SearchResponse(results=ordered)
