"""Helpers to read and rewrite the string values inside tool arguments and tool results.

Security middlewares work per JSON string value (D10). A tool result carries the same data twice:
``structured_content`` (a dict) and ``content`` (text items, usually the same object as JSON text).
Both copies must be inspected and rewritten consistently, otherwise the agent could read the
unmasked copy.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

from mcp.types import CallToolResult, TextContent

Path = tuple[str | int, ...]
StringFn = Callable[[Path, str], str]


def iter_strings(obj: Any, path: Path = ()) -> Iterator[tuple[Path, str]]:
    """Yield ``(path, value)`` for every string inside nested dicts/lists (dict keys are not yielded)."""
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            yield from iter_strings(value, (*path, str(key)))
    elif isinstance(obj, list | tuple):
        for i, value in enumerate(obj):
            yield from iter_strings(value, (*path, i))


def map_strings(obj: Any, fn: StringFn, path: Path = ()) -> Any:
    """Return a copy of ``obj`` where every string ``s`` at ``path`` is replaced by ``fn(path, s)``."""
    if isinstance(obj, str):
        return fn(path, obj)
    if isinstance(obj, dict):
        return {key: map_strings(value, fn, (*path, str(key))) for key, value in obj.items()}
    if isinstance(obj, list | tuple):
        return [map_strings(value, fn, (*path, i)) for i, value in enumerate(obj)]
    return obj


def last_key(path: Path) -> str | None:
    """The nearest dict key on ``path`` (e.g. ``("holdings", 3, "isin")`` -> ``"isin"``)."""
    for part in reversed(path):
        if isinstance(part, str):
            return part
    return None


def _parse_json(text: str) -> tuple[bool, Any]:
    stripped = text.lstrip()
    if not stripped or stripped[0] not in "{[":
        return False, None
    try:
        return True, json.loads(text)
    except ValueError:
        return False, None


def result_strings(result: CallToolResult) -> Iterator[tuple[Path, str]]:
    """Every string a client could read in a result.

    Paths start with ``"structured"`` or ``("content", i)``; text items holding JSON are walked
    per value, other text items are yielded whole.
    """
    if result.structured_content is not None:
        yield from iter_strings(result.structured_content, ("structured",))
    for i, item in enumerate(result.content):
        if isinstance(item, TextContent):
            is_json, data = _parse_json(item.text)
            if is_json:
                yield from iter_strings(data, ("content", i))
            else:
                yield ("content", i), item.text


def map_result_strings(result: CallToolResult, fn: StringFn) -> tuple[CallToolResult, bool]:
    """Apply ``fn`` to every string of a result (same paths as ``result_strings``).

    Returns the new result and whether anything changed. JSON text items are re-serialized only
    when one of their values changed, keeping the original indentation style.
    """
    changed = False

    def tracking(path: Path, value: str) -> str:
        nonlocal changed
        new = fn(path, value)
        if new != value:
            changed = True
        return new

    structured = result.structured_content
    if structured is not None:
        structured = map_strings(structured, tracking, ("structured",))
    content: list[Any] = []
    for i, item in enumerate(result.content):
        if not isinstance(item, TextContent):
            content.append(item)
            continue
        is_json, data = _parse_json(item.text)
        before = changed
        changed = False
        if is_json:
            new_data = map_strings(data, tracking, ("content", i))
            text = (
                json.dumps(new_data, indent=2 if "\n" in item.text else None, ensure_ascii=False)
                if changed
                else item.text
            )
        else:
            text = tracking(("content", i), item.text)
        content.append(item.model_copy(update={"text": text}) if changed else item)
        changed = changed or before
    if not changed:
        return result, False
    return result.model_copy(update={"structured_content": structured, "content": content}), True
