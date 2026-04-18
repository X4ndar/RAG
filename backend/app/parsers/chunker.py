"""Token-aware recursive markdown chunker.

Produces chunks of at most `max_tokens` BGE-M3 tokens, with approximately
`overlap_tokens` carried between consecutive chunks. Chunks shorter than
`min_tokens` are dropped (header/footer noise).

The splitter tries delimiters in priority order (paragraph > line >
sentence); if no delimiter breaks a stubborn segment it hard-splits by
token offsets. Every emitted chunk carries back-pointers (`char_start`,
`char_end`) to the original markdown string so we can trace citations
in later phases.

Token counts come from the BGE-M3 tokenizer itself (not a character
heuristic). The tokenizer is loaded once and cached with `lru_cache`.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from transformers import AutoTokenizer, PreTrainedTokenizerFast

EMBEDDING_MODEL = "BAAI/bge-m3"
DELIMITERS: tuple[str, ...] = ("\n\n", "\n", ". ", "。", "! ", "? ")

DEFAULT_MAX_TOKENS = 512
DEFAULT_OVERLAP_TOKENS = 64
DEFAULT_MIN_TOKENS = 20


@dataclass(frozen=True)
class ChunkSpec:
    """One produced chunk.

    `char_start`/`char_end` are offsets into the source markdown passed to
    `chunk_markdown()`. Consecutive chunks may overlap in char space; that's
    the overlap by design.
    """

    text: str
    char_start: int
    char_end: int
    token_count: int


@lru_cache(maxsize=1)
def _tokenizer() -> PreTrainedTokenizerFast:
    """Load and cache the BGE-M3 tokenizer. One load per process.

    The tokenizer must be a fast (Rust) tokenizer so we can use
    `return_offsets_mapping` for hard-split fallback.
    """
    tok = AutoTokenizer.from_pretrained(EMBEDDING_MODEL)
    if not isinstance(tok, PreTrainedTokenizerFast):
        raise RuntimeError(
            f"{EMBEDDING_MODEL} tokenizer is not a fast tokenizer; "
            "offset mapping required for hard-split fallback"
        )
    return tok


def count_tokens(text: str) -> int:
    """Return the BGE-M3 token count for `text` (no special tokens)."""
    return len(_tokenizer().encode(text, add_special_tokens=False))


Segment = tuple[int, int, int]  # (char_start, char_end, token_count)


def _split_by_delimiter(text: str, start_offset: int, delimiter: str) -> list[tuple[int, int]]:
    """Split `text` on `delimiter`, keeping the delimiter attached to the
    preceding segment. Returns (char_start, char_end) pairs in the original
    coordinate system (`start_offset` is the absolute start of `text`).
    """
    if not text:
        return []
    segments: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(text):
        idx = text.find(delimiter, cursor)
        if idx == -1:
            segments.append((start_offset + cursor, start_offset + len(text)))
            break
        end = idx + len(delimiter)
        segments.append((start_offset + cursor, start_offset + end))
        cursor = end
    return segments


def _hard_split(text: str, start_offset: int, max_tokens: int) -> list[Segment]:
    """Last-resort splitter for text with no usable delimiters.

    Uses `return_offsets_mapping` to slice at token boundaries while keeping
    char offsets in the source coordinate system.
    """
    enc = _tokenizer()(text, add_special_tokens=False, return_offsets_mapping=True)
    offsets: list[tuple[int, int]] = enc["offset_mapping"]
    if not offsets:
        return []

    pieces: list[Segment] = []
    i = 0
    while i < len(offsets):
        window = offsets[i : i + max_tokens]
        s = start_offset + window[0][0]
        e = start_offset + window[-1][1]
        pieces.append((s, e, len(window)))
        i += max_tokens
    return pieces


def _recursive_segments(
    text: str,
    start_offset: int,
    max_tokens: int,
    delimiter_idx: int = 0,
) -> list[Segment]:
    """Produce atomic segments, each ≤ `max_tokens`, by recursively applying
    the delimiter priority list and finally hard-splitting.
    """
    if not text:
        return []

    tokens = count_tokens(text)
    if tokens <= max_tokens:
        return [(start_offset, start_offset + len(text), tokens)]

    if delimiter_idx >= len(DELIMITERS):
        return _hard_split(text, start_offset, max_tokens)

    delimiter = DELIMITERS[delimiter_idx]
    pieces = _split_by_delimiter(text, start_offset, delimiter)
    if len(pieces) <= 1:
        return _recursive_segments(text, start_offset, max_tokens, delimiter_idx + 1)

    out: list[Segment] = []
    for s, e in pieces:
        sub = text[s - start_offset : e - start_offset]
        out.extend(_recursive_segments(sub, s, max_tokens, delimiter_idx + 1))
    return out


def _overlap_start(
    markdown: str,
    prev_start: int,
    prev_end: int,
    overlap_tokens: int,
) -> int:
    """Return the char position in `markdown`, within the previous chunk's
    span, that is `overlap_tokens` before its end. If the previous chunk is
    shorter than `overlap_tokens`, overlap begins at the previous chunk's
    start.
    """
    prev_text = markdown[prev_start:prev_end]
    enc = _tokenizer()(prev_text, add_special_tokens=False, return_offsets_mapping=True)
    offsets: list[tuple[int, int]] = enc["offset_mapping"]
    if len(offsets) <= overlap_tokens:
        return prev_start
    overlap_local_start = offsets[-overlap_tokens][0]
    return prev_start + overlap_local_start


def chunk_markdown(
    markdown: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    min_tokens: int = DEFAULT_MIN_TOKENS,
) -> list[ChunkSpec]:
    """Chunk `markdown` into overlapping token-aware chunks.

    Args:
        markdown: Input text (typically Docling's `export_to_markdown()` output).
        max_tokens: Hard cap on tokens per chunk (BGE-M3 tokens).
        overlap_tokens: Approximate tokens of previous-chunk tail prepended to
            each subsequent chunk.
        min_tokens: Chunks below this size are discarded.

    Returns:
        Ordered list of `ChunkSpec`. Consecutive chunks overlap in char space.
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be in [0, max_tokens)")
    if min_tokens < 0:
        raise ValueError("min_tokens must be non-negative")

    if not markdown.strip():
        return []

    # Pack at most (max_tokens - overlap_tokens) of NEW content per chunk so
    # that once we prepend ~overlap_tokens from the previous chunk we stay
    # under the max_tokens cap. Atomic segments are also bounded by this
    # budget, otherwise a single long segment could blow the cap.
    effective_budget = max_tokens - overlap_tokens

    segments = _recursive_segments(markdown, 0, effective_budget)
    if not segments:
        return []

    chunks: list[ChunkSpec] = []
    cur_start: int | None = None
    cur_end: int | None = None
    cur_tokens = 0

    def emit(start: int, end: int) -> None:
        final_start = start
        if chunks and overlap_tokens > 0:
            prev = chunks[-1]
            overlap_start = _overlap_start(markdown, prev.char_start, prev.char_end, overlap_tokens)
            if overlap_start < final_start:
                final_start = overlap_start
        text = markdown[final_start:end]
        chunks.append(
            ChunkSpec(
                text=text,
                char_start=final_start,
                char_end=end,
                token_count=count_tokens(text),
            )
        )

    for s, e, t in segments:
        if cur_start is None:
            cur_start, cur_end, cur_tokens = s, e, t
            continue
        if cur_tokens + t > effective_budget:
            assert cur_end is not None
            emit(cur_start, cur_end)
            cur_start, cur_end, cur_tokens = s, e, t
        else:
            cur_end = e
            cur_tokens += t

    if cur_start is not None and cur_end is not None:
        emit(cur_start, cur_end)

    return [c for c in chunks if c.token_count >= min_tokens]
