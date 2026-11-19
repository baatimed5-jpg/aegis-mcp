"""Compact serialization of tool results for the LLM.

``json``: pretty JSON (what servers usually return). ``compact``: minified JSON where every list of
objects with the same keys becomes a table ``{"columns": [...], "rows": [[...], ...]}``, so field
names are written once instead of once per row. Both are lossless (the table keeps every value).
"""

from __future__ import annotations

import json
from typing import Any, Literal

Format = Literal["json", "compact"]


def tabulate(obj: Any) -> Any:
    """Recursively turn homogeneous lists of dicts (2+ rows) into column/row tables."""
    if isinstance(obj, dict):
        return {k: tabulate(v) for k, v in obj.items()}
    if isinstance(obj, list):
        items = [tabulate(v) for v in obj]
        if len(items) >= 2 and all(isinstance(i, dict) for i in items):
            columns = list(items[0])
            if all(list(i) == columns for i in items):
                return {"columns": columns, "rows": [[i[c] for c in columns] for i in items]}
        return items
    return obj


def render(data: Any, fmt: Format) -> str:
    """Text sent to the agent for a structured result."""
    if fmt == "compact":
        return json.dumps(tabulate(data), separators=(",", ":"), ensure_ascii=False, default=str)
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)
