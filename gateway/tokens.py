"""In-path token counting with tiktoken (D6: fast and local; provider counts are used offline)."""

from __future__ import annotations

from functools import lru_cache

import tiktoken
from mcp.types import CallToolResult, TextContent

ENCODING = "o200k_base"


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding(ENCODING)


def count_tokens(text: str) -> int:
    """Number of tokens of ``text`` (o200k_base)."""
    return len(_encoding().encode(text, disallowed_special=()))


def result_text(result: CallToolResult) -> str:
    """The text an LLM agent reads from a tool result (all text items, joined)."""
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


def result_tokens(result: CallToolResult) -> int:
    """Tokens of the model-visible text of a tool result."""
    return count_tokens(result_text(result))
