"""Unit tests for the BGE-M3 dense embedding service.

Covers:
    * Output shape is (len(inputs), 1024).
    * The dimension assertion is enforced on first call, not merely declared.
    * Deterministic output: embedding the same text twice yields cosine
      similarity effectively 1.0. Pinned to `device="cpu"`, so flakes here
      mean a real regression, not provider nondeterminism; do not relax
      the threshold.

First run downloads ~2 GB of weights; subsequent runs hit the cache
directory in `settings.embedding_cache_dir`.
"""

from __future__ import annotations

import math

import pytest

from app.embeddings import bge_m3
from app.embeddings.bge_m3 import VECTOR_SIZE, embed, embed_query


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb)


@pytest.fixture(autouse=True)
def _reset_dim_verified() -> None:
    """Each test exercises the assertion path cleanly."""
    bge_m3._dim_verified = False


async def test_embed_returns_correct_shape() -> None:
    vectors = await embed(["bonjour le monde"])
    assert len(vectors) == 1
    assert len(vectors[0]) == VECTOR_SIZE == 1024
    assert all(isinstance(x, float) for x in vectors[0])


async def test_embed_query_is_single_vector() -> None:
    vector = await embed_query("une requete")
    assert len(vector) == VECTOR_SIZE


async def test_embed_is_deterministic() -> None:
    """Same input twice yields (near-)identical vectors under the CPU provider.

    This is the contract we rely on for cache hits and for tests to remain
    green across runs. If this flakes, inspect the ONNX provider — do not
    relax the threshold.
    """
    text = "la meme phrase exactement"
    first = await embed([text])
    second = await embed([text])
    similarity = _cosine(first[0], second[0])
    assert similarity > 0.99, f"cosine similarity degraded to {similarity}"


async def test_empty_input_is_empty_output() -> None:
    assert await embed([]) == []


async def test_dim_mismatch_raises_and_does_not_poison_cache() -> None:
    """If the model somehow returned a wrong-sized vector, we must fail loud
    rather than silently write mismatched points to Qdrant.
    """
    wrong_vector = [0.0] * (VECTOR_SIZE - 1)
    with pytest.raises(RuntimeError, match="Refusing to proceed"):
        bge_m3._verify_dim(wrong_vector)
    # After a failure we must NOT have flipped the sentinel, so the next
    # legitimate call still re-checks.
    assert bge_m3._dim_verified is False
