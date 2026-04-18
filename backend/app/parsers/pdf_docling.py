"""Docling-backed PDF parser.

V1 uses Docling in its fast, programmatic-PDF mode: OCR disabled, table
structure enabled, pypdfium backend. If the markdown is empty after parse,
we treat the PDF as scanned and raise `EmptyParseError`. OCR fallback is a
V2 decision.

Docling runs synchronously under the hood; `parse_pdf` wraps it in a worker
thread so the asyncio event loop is not blocked.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParsedDocument:
    """Output of the parser: the text to chunk plus metadata for the
    `documents` row.
    """

    markdown: str
    page_count: int
    docling_version: str


class EmptyParseError(ValueError):
    """Docling produced no extractable text, likely a scanned PDF."""


@lru_cache(maxsize=1)
def _converter() -> DocumentConverter:
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=PdfPipelineOptions(
                    do_ocr=False,
                    do_table_structure=True,
                ),
                backend=PyPdfiumDocumentBackend,
            ),
        },
    )


def _docling_version() -> str:
    try:
        return _pkg_version("docling")
    except PackageNotFoundError:
        return "unknown"


def _page_count(document: object) -> int:
    """Pull a page count from a Docling `DoclingDocument` defensively.

    The attribute name has shifted between Docling releases; fall back to 0
    if neither shape is present so a parse doesn't fail on metadata alone.
    """
    pages = getattr(document, "pages", None)
    if pages is None:
        return 0
    try:
        return len(pages)
    except TypeError:
        return 0


def _parse_sync(path: Path) -> ParsedDocument:
    result = _converter().convert(str(path))
    markdown = result.document.export_to_markdown()

    logger.debug(
        "docling_output",
        extra={
            "length": len(markdown),
            "preview": markdown[:200],
        },
    )

    if not markdown.strip():
        raise EmptyParseError("no_text_extracted")

    return ParsedDocument(
        markdown=markdown,
        page_count=_page_count(result.document),
        docling_version=_docling_version(),
    )


async def parse_pdf(path: Path) -> ParsedDocument:
    """Parse a PDF into markdown + metadata.

    Args:
        path: Absolute path to a PDF on disk.

    Returns:
        A `ParsedDocument`.

    Raises:
        EmptyParseError: if Docling returns empty or whitespace-only markdown.
    """
    return await asyncio.to_thread(_parse_sync, path)
