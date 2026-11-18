"""Unit tests for WealthService (tool logic without MCP)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from servers.wealth.data import generate_dataset
from servers.wealth.models import Period
from servers.wealth.server import WealthService


@pytest.fixture(scope="module")
def svc() -> WealthService:
    return WealthService(generate_dataset())


def test_get_client_by_id_and_name(svc: WealthService) -> None:
    c = svc.get_client(client_id="CL-0003")
    assert svc.get_client(name=c.name.upper()) == c


@pytest.mark.parametrize("kwargs", [{}, {"client_id": "CL-9999"}, {"name": "zzzz-nobody"}])
def test_get_client_errors(svc: WealthService, kwargs: dict[str, str]) -> None:
    with pytest.raises(ToolError):
        svc.get_client(**kwargs)


def test_get_client_ambiguous_name(svc: WealthService) -> None:
    with pytest.raises(ToolError, match="Several clients"):
        svc.get_client(name="a")


def test_list_portfolios(svc: WealthService) -> None:
    res = svc.list_portfolios("CL-0001")
    client = svc.get_client(client_id="CL-0001")
    assert [p.portfolio_id for p in res.portfolios] == client.portfolio_ids
    assert res.total_market_value == pytest.approx(sum(p.market_value for p in res.portfolios), abs=0.01)


def test_get_holdings_sorted(svc: WealthService) -> None:
    res = svc.get_holdings("PF-00001")
    values = [h.market_value for h in res.holdings]
    assert values == sorted(values, reverse=True)
    with pytest.raises(ToolError):
        svc.get_holdings("PF-99999")


def test_get_transactions_filters(svc: WealthService) -> None:
    start, end = date(2026, 1, 1), date(2026, 6, 30)
    res = svc.get_transactions("PF-00001", start, end, "buy", limit=500)
    assert res.returned == res.total_matching == len(res.transactions)
    assert all(start <= t.trade_date <= end and t.type == "buy" for t in res.transactions)
    assert svc.get_transactions("PF-00001", limit=3).returned == 3


@pytest.mark.parametrize(
    "kwargs", [{"limit": 0}, {"start_date": date(2026, 2, 1), "end_date": date(2026, 1, 1)}]
)
def test_get_transactions_errors(svc: WealthService, kwargs: dict[str, Any]) -> None:
    with pytest.raises(ToolError):
        svc.get_transactions("PF-00001", **kwargs)


@pytest.mark.parametrize(("period", "months"), [("1M", 1), ("3M", 3), ("6M", 6), ("YTD", 10), ("1Y", 12)])
def test_performance_periods(svc: WealthService, period: Period, months: int) -> None:
    res = svc.get_portfolio_performance("PF-00002", period)
    assert len(res.monthly) == months
    assert res.monthly[-1].end_value == pytest.approx(res.end_value, abs=0.05)
    assert res.end_value / res.start_value - 1 == pytest.approx(res.return_pct / 100, abs=1e-4)
    assert res.max_drawdown_pct <= 0
    assert res.excess_return_pct == pytest.approx(res.return_pct - res.benchmark_return_pct, abs=0.02)
    assert len(res.top_contributors) == 5


def _same_currency_pair(svc: WealthService) -> tuple[str, str]:
    pfs = list(svc.ds.portfolios.values())
    for a in pfs:
        for b in pfs:
            if a.portfolio_id != b.portfolio_id and a.currency == b.currency:
                return a.portfolio_id, b.portfolio_id
    raise AssertionError("no same-currency pair")


def test_transfer_is_simulated_and_changes_nothing(svc: WealthService) -> None:
    src, dst = _same_currency_pair(svc)
    before = svc.ds.portfolios[src].cash_balance
    res = svc.transfer_funds(src, dst, 100.0, "test")
    assert res.status == "simulated"
    assert res.from_cash_after == pytest.approx(before - 100.0)
    assert svc.ds.portfolios[src].cash_balance == before
    assert svc.transfer_funds(src, dst, 100.0, "test").transfer_id == res.transfer_id


def test_transfer_validation(svc: WealthService) -> None:
    src, dst = _same_currency_pair(svc)
    for a, b, amount in [(src, src, 10.0), (src, dst, -1.0), (src, dst, 2_000_000.0), (src, "PF-99999", 1.0)]:
        with pytest.raises(ToolError):
            svc.transfer_funds(a, b, amount)
    with pytest.raises(ToolError, match="Insufficient"):
        svc.transfer_funds(src, dst, svc.ds.portfolios[src].cash_balance + 1)
