"""Unit tests for the token-aware markdown chunker.

Covers the four guarantees from the V1 design note:
    (a) every chunk is ≤ max_tokens
    (b) overlap ≈ overlap_tokens between consecutive chunks
    (c) chunks under min_tokens are dropped
    (d) a no-delimiter 2000-token paragraph still chunks via hard-split fallback
"""

from __future__ import annotations

import dataclasses

import pytest

from app.parsers.chunker import (
    DEFAULT_MAX_TOKENS,
    ChunkSpec,
    _tokenizer,
    chunk_markdown,
    count_tokens,
)


def _tokenize(text: str) -> list[int]:
    return _tokenizer().encode(text, add_special_tokens=False)


def test_short_text_returns_single_chunk() -> None:
    """Input above min_tokens but well below max_tokens produces one chunk
    that spans the whole input.
    """
    text = (
        "This is a paragraph with enough words to clear the default min_tokens "
        "threshold. It contains two sentences on a single topic, no headings, "
        "and no structural breaks worth splitting on."
    )
    assert count_tokens(text) >= 20, "fixture is too short for this test"
    chunks = chunk_markdown(text)
    assert len(chunks) == 1
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == len(text)
    assert chunks[0].text == text


def test_every_chunk_respects_max_tokens() -> None:
    """Gate (a)."""
    paragraph = " ".join(
        [f"La phrase numero {i} parle de quelque chose d'interessant." for i in range(200)]
    )
    markdown = "\n\n".join([paragraph] * 5)

    chunks = chunk_markdown(markdown, max_tokens=256, overlap_tokens=32, min_tokens=20)

    assert len(chunks) > 1, "markdown should produce multiple chunks"
    for c in chunks:
        assert c.token_count <= 256, f"chunk exceeds max_tokens: {c.token_count}"


def test_consecutive_chunks_overlap_by_roughly_overlap_tokens() -> None:
    """Gate (b). Overlap is approximate (greedy packing + token boundaries),
    so we assert a tolerant band.
    """
    paragraph = " ".join([f"Sentence number {i} with some filler content." for i in range(60)])
    markdown = "\n\n".join([paragraph] * 4)

    max_tokens = 200
    overlap_tokens = 40
    chunks = chunk_markdown(markdown, max_tokens=max_tokens, overlap_tokens=overlap_tokens)

    assert len(chunks) >= 2, "need at least two chunks to check overlap"

    for prev, curr in zip(chunks, chunks[1:], strict=False):
        overlap_chars = prev.char_end - curr.char_start
        assert overlap_chars > 0, "consecutive chunks must overlap in char space"
        overlap_text = markdown[curr.char_start : prev.char_end]
        overlap_actual_tokens = count_tokens(overlap_text)
        # Greedy packing + token rounding: accept 0.5x..2x the target.
        assert overlap_tokens * 0.5 <= overlap_actual_tokens <= overlap_tokens * 2, (
            f"overlap out of band: {overlap_actual_tokens} tokens "
            f"(expected around {overlap_tokens})"
        )


def test_chunks_below_min_tokens_are_dropped() -> None:
    """Gate (c). Fragments below min_tokens should not appear."""
    # Construct an input whose natural splits create a tiny trailing piece:
    body = " ".join([f"Longer sentence {i} with plenty of words to count." for i in range(50)])
    tail = "Tiny."  # intentionally under min_tokens
    markdown = f"{body}\n\n{tail}"

    chunks = chunk_markdown(markdown, max_tokens=100, overlap_tokens=10, min_tokens=20)

    for c in chunks:
        assert c.token_count >= 20, f"chunk under min_tokens survived: {c.token_count}"


def test_no_delimiter_long_paragraph_hard_splits() -> None:
    """Gate (d). A 2000-token paragraph with no delimiters must still chunk."""
    # Build a string that is long in tokens but contains none of our delimiters.
    unit = "mot"  # French "word", no punctuation, no newline
    long_paragraph = (" " + unit) * 3000
    long_paragraph = long_paragraph.strip()

    token_count = count_tokens(long_paragraph)
    assert token_count >= 2000, f"fixture not long enough: {token_count} tokens"
    assert "\n" not in long_paragraph
    assert ". " not in long_paragraph

    chunks = chunk_markdown(long_paragraph, max_tokens=DEFAULT_MAX_TOKENS)

    assert len(chunks) >= 2, "hard-split fallback should produce multiple chunks"
    for c in chunks:
        assert c.token_count <= DEFAULT_MAX_TOKENS


def test_empty_input_returns_empty_list() -> None:
    assert chunk_markdown("") == []
    assert chunk_markdown("   \n\n   ") == []


def test_invalid_parameters_raise() -> None:
    with pytest.raises(ValueError):
        chunk_markdown("x", max_tokens=0)
    with pytest.raises(ValueError):
        chunk_markdown("x", max_tokens=100, overlap_tokens=100)
    with pytest.raises(ValueError):
        chunk_markdown("x", min_tokens=-1)


def test_chunkspec_is_immutable() -> None:
    spec = ChunkSpec(text="t", char_start=0, char_end=1, token_count=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.__setattr__("text", "u")
