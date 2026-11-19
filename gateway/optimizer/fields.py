"""Candidate field paths of a structured tool result (the unit the learned pruner keeps or drops).

Paths use the same syntax as the pruning rules: ``a.b`` and ``list[].field``. Top-level objects are
expanded one level (``kyc.status``), lists of objects are expanded into their item fields
(``holdings[].weight``); deeper objects stay one unit (``holdings[].instrument``).
"""

from __future__ import annotations

from typing import Any

PROTECTED = {"_omitted"}


def candidate_paths(data: dict[str, Any]) -> dict[str, Any]:
    """Path -> one example value (the first list item's value for ``[]`` paths)."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key in PROTECTED:
            continue
        if isinstance(value, dict) and value:
            for sub, v in value.items():
                out[f"{key}.{sub}"] = v
        elif isinstance(value, list) and value and all(isinstance(i, dict) for i in value):
            for sub, v in value[0].items():
                out[f"{key}[].{sub}"] = v
        else:
            out[key] = value
    return out


def humanize(path: str) -> str:
    """``holdings[].unrealized_pnl_pct`` -> ``holdings unrealized pnl pct`` (text for embeddings)."""
    return path.replace("[]", "").replace(".", " ").replace("_", " ").strip()


def value_kind(value: Any) -> str:
    """Coarse type of a value."""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return "null"
