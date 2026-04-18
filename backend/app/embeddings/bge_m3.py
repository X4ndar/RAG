"""BGE-M3 dense embeddings via sentence-transformers.

CLAUDE.md permits either FastEmbed or sentence-transformers for the
embedding layer. The V1 design note originally pinned FastEmbed, but
BGE-M3 is not in FastEmbed's current catalog (it ships only the BGE
English family and a Chinese variant), so we use the explicit
sentence-transformers fallback. The switch is CPU-only, deterministic,
and keeps the same public contract (async `embed`, `embed_query`,
`warm_up`, `VECTOR_SIZE`).

Contract:
    * Model is loaded once per process via `lru_cache`, pinned to
      `device="cpu"` for reproducible outputs across runs.
    * Weights cache lives at `settings.embedding_cache_dir` — Docker
      volume `embedding_cache` in-container; `~/.cache/embeddings` on
      the host.
    * `warm_up()` runs the load inside `asyncio.to_thread` so a FastAPI
      startup hook or arq `on_startup` doesn't block the event loop.
    * `embed()` wraps ONLY the inference call in `asyncio.to_thread`;
      the `_model()` call itself is a cache lookup once warmed up.
    * Vector dimension is asserted on the first successful call and fails
      loud on mismatch — rather than silently poisoning the Qdrant
      collection, which is pinned to 1024-dim.
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import Any, cast

from sentence_transformers import SentenceTransformer

from app.core.config import get_settings

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "BAAI/bge-m3"
VECTOR_SIZE = 1024

_dim_verified = False


@lru_cache(maxsize=1)
def _model() -> SentenceTransformer:
    settings = get_settings()
    logger.info(
        "loading_embedding_model",
        extra={"model": EMBEDDING_MODEL, "cache_dir": settings.embedding_cache_dir},
    )
    return cast(
        SentenceTransformer,
        SentenceTransformer(
            EMBEDDING_MODEL,
            cache_folder=settings.embedding_cache_dir,
            device="cpu",
        ),
    )


def _verify_dim(vector: list[float]) -> None:
    """Refuse to run if the model returns an unexpected vector size.

    Checked once per process; after the first successful call this is a
    no-op. Raised early to prevent the Qdrant collection, which is pinned
    at 1024-dim, from being filled with mismatched points.
    """
    global _dim_verified
    if _dim_verified:
        return
    actual = len(vector)
    if actual != VECTOR_SIZE:
        raise RuntimeError(
            f"{EMBEDDING_MODEL} returned a {actual}-dim vector; "
            f"expected {VECTOR_SIZE}. Refusing to proceed: the Qdrant "
            f"collection `documents_v1` is pinned to {VECTOR_SIZE}."
        )
    _dim_verified = True


async def warm_up() -> None:
    """Load the model in a worker thread so the event loop isn't blocked.

    Call from FastAPI startup and arq `on_startup`. Safe to call repeatedly;
    after the first call the cached factory returns synchronously and
    cheaply.
    """
    await asyncio.to_thread(_model)


async def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts. Returns one 1024-dim float list per input.

    `_model()` is a cache lookup once the model has been warmed up, so the
    only real work inside the thread is the sync encode call.
    """
    if not texts:
        return []

    model = _model()

    def _run() -> list[list[float]]:
        result: Any = model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=False,
            show_progress_bar=False,
        )
        return [row.tolist() for row in result]

    vectors = await asyncio.to_thread(_run)
    if vectors:
        _verify_dim(vectors[0])
    return vectors


async def embed_query(text: str) -> list[float]:
    """Embed a single query string. Convenience wrapper over `embed`."""
    results = await embed([text])
    return results[0]
