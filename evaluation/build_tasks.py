"""Generate the 50 assistant benchmark tasks (``evaluation/benchmark/tasks.json``).

Every expected answer is computed from the seeded wealth dataset with the same ``WealthService``
the MCP server uses, so nothing is typed by hand. Clients and portfolios are picked with
``random.Random(42)``. Questions never ask for values the gateway masks (emails, IBANs, phones, ...).

Run: ``uv run python -m evaluation.build_tasks``
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from servers.wealth.data import generate_dataset
from servers.wealth.models import Client
from servers.wealth.server import WealthService

OUT = Path(__file__).parent / "benchmark" / "tasks.json"
SEED = 42
MONEY_REL_TOL = 0.005
"""0.5 %: tolerates rounding of large amounts ("about 1.23 million")."""
PCT_ABS_TOL = 0.051
"""Percentages and ratios are reported with 2 decimals."""

Task = dict[str, Any]


def number(value: float, *, pct: bool = False) -> dict[str, Any]:
    """Checker spec: the answer must contain this number (within tolerance)."""
    if pct:
        return {"type": "number", "value": round(value, 4), "abs_tol": PCT_ABS_TOL}
    return {"type": "number", "value": round(value, 2), "rel_tol": MONEY_REL_TOL}


def text(*groups: list[str]) -> dict[str, Any]:
    """Checker spec: for each group, at least one alternative must appear (case-insensitive)."""
    return {"type": "text", "all_of": [[a for a in g if a] for g in groups]}


def date_forms(d: date) -> list[str]:
    """Common spellings of a date, so the text check does not depend on the agent's date format."""
    month = d.strftime("%B")
    return [
        d.isoformat(),
        f"{month} {d.day}, {d.year}",
        f"{d.day} {month} {d.year}",
        f"{month[:3]} {d.day}, {d.year}",
        f"{d.day} {month[:3]} {d.year}",
        f"{d.day:02d}/{d.month:02d}/{d.year}",
        f"{d.month:02d}/{d.day:02d}/{d.year}",
    ]


def judge(reference: str) -> dict[str, Any]:
    """Checker spec: an LLM judge compares the answer with this reference (free-form answers)."""
    return {"type": "judge", "reference": reference}


class Builder:
    """Picks entities with a seeded RNG and builds tasks of each category."""

    def __init__(self) -> None:
        self.ds = generate_dataset()
        self.svc = WealthService(self.ds)
        self.rng = random.Random(SEED)
        names = [c.name.lower() for c in self.ds.clients.values()]
        # A name is usable only if the substring lookup in get_client finds exactly one client.
        self.clients = [c for c in self.ds.clients.values() if sum(c.name.lower() in n for n in names) == 1]
        self.tasks: list[Task] = []
        self.used: dict[str, set[str]] = {}

    def pick_client(self, category: str, cond: Callable[[Client], bool] = lambda _c: True) -> Client:
        """A client not yet used in this category."""
        seen = self.used.setdefault(category, set())
        pool = [c for c in self.clients if c.client_id not in seen and cond(c)]
        client = self.rng.choice(pool)
        seen.add(client.client_id)
        return client

    def pick_portfolio(self, category: str, cond: Callable[[str], bool] = lambda _p: True) -> str:
        """A portfolio id not yet used in this category."""
        seen = self.used.setdefault(category, set())
        pool = sorted(p for p in self.ds.portfolios if p not in seen and cond(p))
        pid = self.rng.choice(pool)
        seen.add(pid)
        return pid

    def add(self, category: str, question: str, check: dict[str, Any], tools: list[str]) -> None:
        """Append one task with a stable id."""
        self.tasks.append(
            {
                "id": f"T{len(self.tasks) + 1:02d}",
                "category": category,
                "question": question,
                "check": check,
                "expected_tools": tools,
            }
        )

    # ---- single-client facts -------------------------------------------------------------
    def client_total_value(self, n: int) -> None:
        """Sum of a client's portfolio values (list_portfolios)."""
        for _ in range(n):
            c = self.pick_client("total_value")
            pl = self.svc.list_portfolios(c.client_id)
            currencies = {p.currency for p in pl.portfolios}
            note = f" (They are all in {currencies.pop()}.)" if len(currencies) == 1 else ""
            self.add(
                "client_total_value",
                f"What is the total market value of all of {c.name}'s portfolios?{note}",
                number(pl.total_market_value),
                ["get_client", "list_portfolios"],
            )

    def client_risk_profile(self, n: int) -> None:
        """Risk profile word of a client."""
        for _ in range(n):
            c = self.pick_client("risk")
            self.add(
                "client_risk_profile",
                f"Which risk profile is recorded for our client {c.name}?",
                text([c.risk_profile]),
                ["get_client"],
            )

    def client_advisor(self, n: int) -> None:
        """Advisor name and office."""
        for _ in range(n):
            c = self.pick_client("advisor")
            self.add(
                "client_advisor",
                f"Who is the advisor in charge of {c.name}, and which office are they in?",
                text([c.advisor.name], [c.advisor.office]),
                ["get_client"],
            )

    # ---- single-portfolio facts ----------------------------------------------------------
    def portfolio_cash(self, n: int) -> None:
        """Cash balance of a portfolio given only its id."""
        for _ in range(n):
            pid = self.pick_portfolio("cash")
            self.add(
                "portfolio_cash",
                f"How much cash is available in portfolio {pid}?",
                number(self.ds.portfolios[pid].cash_balance),
                ["get_holdings", "list_portfolios"],
            )

    def largest_holding(self, n: int) -> None:
        """Name of the largest position."""
        for _ in range(n):
            pid = self.pick_portfolio("largest")
            top = self.svc.get_holdings(pid).holdings[0]
            self.add(
                "largest_holding",
                f"What is the largest position (by market value) in portfolio {pid}, and its weight?",
                text([top.name, top.ticker]),
                ["get_holdings"],
            )

    def holding_count(self, n: int) -> None:
        """Number of positions."""
        for _ in range(n):
            pid = self.pick_portfolio("count")
            self.add(
                "holding_count",
                f"How many different positions does portfolio {pid} hold?",
                number(len(self.svc.get_holdings(pid).holdings)),
                ["get_holdings"],
            )

    def return_1y(self, n: int) -> None:
        """One-year return in percent."""
        for _ in range(n):
            pid = self.pick_portfolio("ret1y")
            perf = self.svc.get_portfolio_performance(pid, "1Y")
            self.add(
                "return_1y",
                f"What was the one-year return of portfolio {pid}, in percent?",
                number(perf.return_pct, pct=True),
                ["get_portfolio_performance"],
            )

    def excess_ytd(self, n: int) -> None:
        """Excess return over the benchmark, year to date."""
        for _ in range(n):
            pid = self.pick_portfolio("excess")
            perf = self.svc.get_portfolio_performance(pid, "YTD")
            self.add(
                "excess_return_ytd",
                f"Year to date, did portfolio {pid} beat its benchmark? By how many percentage points?",
                {**number(perf.excess_return_pct, pct=True), "signless": True},
                ["get_portfolio_performance"],
            )

    def risk_metric(self, n: int) -> None:
        """Volatility or Sharpe ratio (alternating)."""
        for i in range(n):
            pid = self.pick_portfolio("riskm")
            perf = self.svc.get_portfolio_performance(pid, "1Y")
            if i % 2 == 0:
                question = f"What is the annualised volatility of portfolio {pid} over the last year?"
                value = perf.volatility_pct
            else:
                question = f"What is the one-year Sharpe ratio of portfolio {pid}?"
                value = perf.sharpe_ratio
            self.add("risk_metric", question, number(value, pct=True), ["get_portfolio_performance"])

    def top_contributor(self, n: int) -> None:
        """Best contributor to one-year performance."""
        for _ in range(n):
            pid = self.pick_portfolio("contrib")
            top = self.svc.get_portfolio_performance(pid, "1Y").top_contributors[0]
            self.add(
                "top_contributor",
                f"Which holding contributed the most to portfolio {pid}'s performance over the last year?",
                text([top.name, top.isin]),
                ["get_portfolio_performance"],
            )

    # ---- transactions ----------------------------------------------------------------------
    def dividend_count(self, n: int) -> None:
        """Number of dividends in a date range (total_matching)."""
        start, end = date(2026, 1, 1), date(2026, 10, 30)
        for _ in range(n):
            pid = self.pick_portfolio(
                "div", lambda p: any(t.type == "dividend" for t in self.ds.transactions[p])
            )
            res = self.svc.get_transactions(pid, start, end, "dividend", limit=500)
            self.add(
                "dividend_count",
                f"How many dividend payments did portfolio {pid} receive between {start} and {end}?",
                number(res.total_matching),
                ["get_transactions"],
            )

    def last_sell(self, n: int) -> None:
        """Instrument and date of the most recent sale."""
        for _ in range(n):
            pid = self.pick_portfolio(
                "sell", lambda p: any(t.type == "sell" for t in self.ds.transactions[p])
            )
            tx = self.svc.get_transactions(pid, tx_type="sell", limit=1).transactions[0]
            self.add(
                "last_sell",
                f"What was the most recent sale in portfolio {pid}: which instrument, and on what date?",
                text([tx.instrument_name or "", tx.ticker or ""], date_forms(tx.trade_date)),
                ["get_transactions"],
            )

    # ---- multi-step ------------------------------------------------------------------------
    def best_portfolio(self, n: int) -> None:
        """Compare the one-year return of every portfolio of a client."""
        for _ in range(n):
            c = self.pick_client("best", lambda c: len(c.portfolio_ids) >= 2)
            perfs = {p: self.svc.get_portfolio_performance(p, "1Y").return_pct for p in c.portfolio_ids}
            best = max(perfs, key=lambda p: perfs[p])
            self.add(
                "best_portfolio",
                f"{c.name} has several portfolios. Which one had the best one-year return, and how much?",
                text([best]),
                ["get_client", "get_portfolio_performance"],
            )

    def most_cash(self, n: int) -> None:
        """Portfolio of a client with the most cash."""
        for _ in range(n):
            c = self.pick_client("mostcash", lambda c: len(c.portfolio_ids) >= 2)
            best = max((self.ds.portfolios[p] for p in c.portfolio_ids), key=lambda p: p.cash_balance)
            self.add(
                "most_cash",
                f"Which of {c.name}'s portfolios holds the most cash, and how much?",
                text([best.portfolio_id]),
                ["get_client", "list_portfolios"],
            )

    def _transfer_pairs(self, c: Client) -> list[tuple[str, str]]:
        pfs = self.ds.portfolios
        return [
            (a, b)
            for a in c.portfolio_ids
            for b in c.portfolio_ids
            if a != b and pfs[a].currency == pfs[b].currency and pfs[a].cash_balance >= 2000
        ]

    def transfer(self, n: int) -> None:
        """Simulated transfer (needs approval through the gateway)."""
        for _ in range(n):
            c = self.pick_client("transfer", lambda c: bool(self._transfer_pairs(c)))
            src, dst = self._transfer_pairs(c)[0]
            pf = self.ds.portfolios[src]
            amount = float(max(1000, round(pf.cash_balance * 0.1, -3)))
            res = self.svc.transfer_funds(src, dst, amount, "benchmark")
            self.add(
                "transfer_simulation",
                f"Please simulate a transfer of {amount:,.0f} {pf.currency} from portfolio {src} to {dst} "
                f"with reference 'benchmark'. How much cash would remain in {src}?",
                number(res.from_cash_after),
                ["transfer_funds"],
            )

    # ---- free-form (LLM judge) -------------------------------------------------------------
    def esg_check(self, n: int) -> None:
        """Holdings that fall in the client's excluded sectors."""
        for _ in range(n):
            c = self.pick_client("esg", lambda c: bool(c.preferences.excluded_sectors))
            excluded = set(c.preferences.excluded_sectors)
            hits = sorted(
                {h.name for p in c.portfolio_ids for h in self.ds.holdings[p] if h.sector in excluded}
            )
            found = f"Holdings in those sectors: {', '.join(hits)}." if hits else "No holding is in them."
            self.add(
                "esg_exclusions",
                f"Does {c.name} hold anything in the sectors they asked to exclude? List those holdings.",
                judge(f"Excluded sectors: {', '.join(sorted(excluded))}. {found}"),
                ["get_client", "get_holdings"],
            )

    def kyc(self, n: int) -> None:
        """KYC status and next review date."""
        for _ in range(n):
            c = self.pick_client("kyc")
            self.add(
                "kyc_status",
                f"What is the KYC status of {c.name} and when is the next KYC review due?",
                judge(f"KYC status: {c.kyc.status}. Next review: {c.kyc.next_review.isoformat()}."),
                ["get_client"],
            )

    def unknown_client(self) -> None:
        """A client that does not exist: the agent must not invent an answer."""
        self.add(
            "unknown_client",
            "What is the total portfolio value of our client Zorblax Quintavalle?",
            judge(
                "There is no client named Zorblax Quintavalle; the assistant must say it cannot find "
                "this client and must not invent a value."
            ),
            ["get_client"],
        )

    def build(self) -> list[Task]:
        """All categories; the counts add up to 50."""
        self.client_total_value(4)
        self.client_risk_profile(3)
        self.client_advisor(3)
        self.portfolio_cash(3)
        self.largest_holding(3)
        self.holding_count(3)
        self.return_1y(3)
        self.excess_ytd(3)
        self.risk_metric(3)
        self.top_contributor(3)
        self.dividend_count(3)
        self.last_sell(3)
        self.best_portfolio(3)
        self.most_cash(2)
        self.transfer(3)
        self.esg_check(2)
        self.kyc(2)
        self.unknown_client()
        return self.tasks


def main() -> None:
    """Write the task file and print a per-category summary."""
    tasks = Builder().build()
    if len(tasks) != 50:
        raise SystemExit(f"expected 50 tasks, built {len(tasks)}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(tasks, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for t in tasks:
        counts[t["category"]] = counts.get(t["category"], 0) + 1
    print(f"{len(tasks)} tasks -> {OUT}")
    for cat, k in counts.items():
        print(f"  {cat}: {k}")


if __name__ == "__main__":
    main()
