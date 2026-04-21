"""Pydantic request/response schemas for the documents API."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class DocumentResponse(BaseModel):
    """Public view of a Document row."""

    id: UUID
    status: str
    filename: str
    uploaded_at: datetime
    parsed_at: datetime | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
