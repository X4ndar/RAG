"""Pydantic schemas for the search API."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class SearchHit(BaseModel):
    """One result in a search response."""

    chunk_id: UUID
    document_id: UUID
    chunk_index: int
    text: str
    score: float
    page_number: int | None = None


class SearchResponse(BaseModel):
    """Envelope for `/api/v1/search` results."""

    results: list[SearchHit]
