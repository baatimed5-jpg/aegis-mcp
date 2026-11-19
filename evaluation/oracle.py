"""Reference ("oracle") tool calls of each benchmark task: what a correct agent must call.

Derived deterministically from the task's category and the ids/names in its question, using the
same seeded dataset as the server. Used by the offline replay (token savings of every profile
without LLM calls) and by the learned-pruning labeler (Phase 7).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from evaluation.build_tasks import OUT as TASKS_FILE
from servers.wealth.data import WealthDataset, generate_dataset

Call = tuple[str, dict[str, Any]]
_PF = re.compile(r"PF-\d{5}")
_DATES = re.compile(r"\d{4}-\d{2}-\d{2}")
_AMOUNT = re.compile(r"transfer of ([\d,]+)")


@lru_cache(maxsize=1)
def dataset() -> WealthDataset:
    """The seeded dataset (same as the wealth server)."""
    return generate_dataset()


def _client_in(question: str) -> Any:
    matches = [c for c in dataset().clients.values() if c.name in question]
    return max(matches, key=lambda c: len(c.name)) if matches else None


def oracle_calls(task: dict[str, Any]) -> list[Call]:
    """Public tool names (``wealth__*``) and arguments a correct agent needs for this task."""
    q, cat = task["question"], task["category"]
    pfs = _PF.findall(q)
    client = _client_in(q)
    calls: list[Call] = []
    if cat == "unknown_client":
        return [("wealth__get_client", {"name": "Zorblax Quintavalle"})]
    if client is not None:
        calls.append(("wealth__get_client", {"name": client.name}))
    if cat in ("client_total_value", "most_cash"):
        calls.append(("wealth__list_portfolios", {"client_id": client.client_id}))
    elif cat == "portfolio_cash":
        owner = dataset().portfolios[pfs[0]].client_id
        calls.append(("wealth__list_portfolios", {"client_id": owner}))
    elif cat in ("largest_holding", "holding_count"):
        calls.append(("wealth__get_holdings", {"portfolio_id": pfs[0]}))
    elif cat in ("return_1y", "risk_metric", "top_contributor"):
        calls.append(("wealth__get_portfolio_performance", {"portfolio_id": pfs[0], "period": "1Y"}))
    elif cat == "excess_return_ytd":
        calls.append(("wealth__get_portfolio_performance", {"portfolio_id": pfs[0], "period": "YTD"}))
    elif cat == "dividend_count":
        start, end = _DATES.findall(q)[:2]
        args = {
            "portfolio_id": pfs[0],
            "start_date": start,
            "end_date": end,
            "tx_type": "dividend",
            "limit": 500,
        }
        calls.append(("wealth__get_transactions", args))
    elif cat == "last_sell":
        calls.append(("wealth__get_transactions", {"portfolio_id": pfs[0], "tx_type": "sell"}))
    elif cat == "best_portfolio":
        calls += [
            ("wealth__get_portfolio_performance", {"portfolio_id": p, "period": "1Y"})
            for p in client.portfolio_ids
        ]
    elif cat == "esg_exclusions":
        calls += [("wealth__get_holdings", {"portfolio_id": p}) for p in client.portfolio_ids]
    elif cat == "transfer_simulation":
        amount = float(_AMOUNT.search(q).group(1).replace(",", ""))  # type: ignore[union-attr]
        args = {
            "from_portfolio_id": pfs[0],
            "to_portfolio_id": pfs[1],
            "amount": amount,
            "reference": "benchmark",
        }
        calls.append(("wealth__transfer_funds", args))
    return calls


def corpus() -> list[tuple[dict[str, Any], str, dict[str, Any]]]:
    """(task, tool, arguments) for every oracle call of every benchmark task."""
    tasks: list[dict[str, Any]] = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    return [(t, tool, args) for t in tasks for tool, args in oracle_calls(t)]
