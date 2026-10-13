"""Shared by the chunkers: the token measure and the chunk format."""
import math

from rag import config


def approx_tokens(text: str) -> int:
    """Approximate token count: characters divided by config.CHARS_PER_TOKEN, rounded up."""
    return math.ceil(len(text) / config.CHARS_PER_TOKEN)


def make_chunk(document: str, page: int, page_label: str | None, section: str | None, kind: str, text: str) -> dict:
    """One chunk. `page` is the PDF page position (1-based); `kind` is "text" or "table"."""
    return {
        "document": document,
        "page": page,
        "page_label": page_label,
        "section": section,
        "kind": kind,
        "text": text,
    }
