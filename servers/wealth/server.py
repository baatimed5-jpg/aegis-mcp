"""Wealth demo MCP server: 6 tools over the fictional in-memory dataset.

``WealthService`` holds the business logic (plain Python, easy to unit test);
``build_server`` registers it as MCP tools on an ``MCPServer`` (mcp SDK 2.x).
"""

from __future__ import annotations

import hashlib
import math
from datetime import date

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from servers.wealth.data import AS_OF, WealthDataset, annualized_volatility, generate_dataset, max_drawdown
from servers.wealth.models import (
    Client,
    Contributor,
    HoldingsResult,
    MonthlyReturn,
    PerformanceResult,
    Period,
    PortfolioList,
    TransactionsResult,
    TransferResult,
    TxType,
)

PERIOD_MONTHS: dict[str, int] = {"1M": 1, "3M": 3, "6M": 6, "YTD": AS_OF.month, "1Y": 12}
RISK_FREE_RATE = 0.02
MAX_TRANSFER = 1_000_000.0


class WealthService:
    """Read-only queries plus a simulated transfer over a ``WealthDataset``."""

    def __init__(self, dataset: WealthDataset) -> None:
        self.ds = dataset

    def _portfolio(self, portfolio_id: str) -> str:
        if portfolio_id not in self.ds.portfolios:
            raise ToolError(f"Unknown portfolio_id: {portfolio_id}")
        return portfolio_id

    def get_client(self, client_id: str | None = None, name: str | None = None) -> Client:
        """Return a client by id, or by a unique case-insensitive name match."""
        if client_id:
            client = self.ds.clients.get(client_id)
            if client is None:
                raise ToolError(f"Unknown client_id: {client_id}")
            return client
        if name:
            needle = name.strip().lower()
            matches = [c for c in self.ds.clients.values() if needle in c.name.lower()]
            if not matches:
                raise ToolError(f"No client matches name: {name}")
            if len(matches) > 1:
                options = ", ".join(f"{c.client_id} ({c.name})" for c in matches)
                raise ToolError(f"Several clients match '{name}': {options}. Use client_id.")
            return matches[0]
        raise ToolError("Provide client_id or name.")

    def list_portfolios(self, client_id: str) -> PortfolioList:
        """Return all portfolios of a client."""
        client = self.get_client(client_id=client_id)
        portfolios = [self.ds.portfolios[p] for p in client.portfolio_ids]
        return PortfolioList(
            client_id=client.client_id,
            client_name=client.name,
            total_market_value=round(sum(p.market_value for p in portfolios), 2),
            portfolios=portfolios,
        )

    def get_holdings(self, portfolio_id: str) -> HoldingsResult:
        """Return all positions of a portfolio, largest first."""
        pf = self.ds.portfolios[self._portfolio(portfolio_id)]
        holdings = sorted(self.ds.holdings[portfolio_id], key=lambda h: h.market_value, reverse=True)
        return HoldingsResult(
            portfolio_id=portfolio_id,
            as_of=AS_OF,
            currency=pf.currency,
            total_market_value=pf.market_value,
            holdings=holdings,
        )

    def get_transactions(
        self,
        portfolio_id: str,
        start_date: date | None = None,
        end_date: date | None = None,
        tx_type: TxType | None = None,
        limit: int = 50,
    ) -> TransactionsResult:
        """Return transactions (newest first) filtered by date range and type."""
        self._portfolio(portfolio_id)
        if start_date and end_date and start_date > end_date:
            raise ToolError("start_date must be on or before end_date.")
        if not 1 <= limit <= 500:
            raise ToolError("limit must be between 1 and 500.")
        matching = [
            t
            for t in self.ds.transactions[portfolio_id]
            if (start_date is None or t.trade_date >= start_date)
            and (end_date is None or t.trade_date <= end_date)
            and (tx_type is None or t.type == tx_type)
        ]
        return TransactionsResult(
            portfolio_id=portfolio_id,
            start_date=start_date,
            end_date=end_date,
            total_matching=len(matching),
            returned=min(limit, len(matching)),
            transactions=matching[:limit],
        )

    def get_portfolio_performance(self, portfolio_id: str, period: Period = "1Y") -> PerformanceResult:
        """Compute return, risk and contributors of a portfolio over a period ending at AS_OF."""
        pf = self.ds.portfolios[self._portfolio(portfolio_id)]
        if period not in PERIOD_MONTHS:
            raise ToolError(f"Unknown period: {period}")
        n = PERIOD_MONTHS[period]
        window = self.ds.monthly_returns[portfolio_id][-n:]
        p_rets = [p for _, p, _ in window]
        b_rets = [b for _, _, b in window]
        total = math.prod(1 + r for r in p_rets) - 1
        bench = math.prod(1 + r for r in b_rets) - 1
        start_value = pf.market_value / (1 + total)

        monthly: list[MonthlyReturn] = []
        value = start_value
        for month, p, b in window:
            value *= 1 + p
            monthly.append(
                MonthlyReturn(
                    month=month,
                    portfolio_return_pct=round(p * 100, 2),
                    benchmark_return_pct=round(b * 100, 2),
                    end_value=round(value, 2),
                )
            )
        vol = annualized_volatility(p_rets)
        annualized = (1 + total) ** (12 / n) - 1
        sharpe = (annualized - RISK_FREE_RATE) / (vol / 100) if vol > 0 else 0.0
        contributors = sorted(
            (
                Contributor(isin=isin, name=name, contribution_pct=round(share * total * 100, 3))
                for isin, name, share in self.ds.contribution_shares[portfolio_id]
            ),
            key=lambda c: c.contribution_pct,
            reverse=True,
        )
        return PerformanceResult(
            portfolio_id=portfolio_id,
            period=period,
            as_of=AS_OF,
            currency=pf.currency,
            start_value=round(start_value, 2),
            end_value=pf.market_value,
            return_pct=round(total * 100, 2),
            benchmark=pf.benchmark,
            benchmark_return_pct=round(bench * 100, 2),
            excess_return_pct=round((total - bench) * 100, 2),
            volatility_pct=round(vol, 2),
            max_drawdown_pct=round(max_drawdown(p_rets), 2),
            sharpe_ratio=round(sharpe, 2),
            monthly=monthly,
            top_contributors=contributors[:5],
            bottom_contributors=list(reversed(contributors[-5:])),
        )

    def transfer_funds(
        self, from_portfolio_id: str, to_portfolio_id: str, amount: float, reference: str = ""
    ) -> TransferResult:
        """SIMULATE a cash transfer between two portfolios. Nothing is changed or executed."""
        src = self.ds.portfolios[self._portfolio(from_portfolio_id)]
        dst = self.ds.portfolios[self._portfolio(to_portfolio_id)]
        if from_portfolio_id == to_portfolio_id:
            raise ToolError("Source and destination portfolios must differ.")
        if not 0 < amount <= MAX_TRANSFER:
            raise ToolError(f"amount must be > 0 and <= {MAX_TRANSFER:,.0f}.")
        if src.currency != dst.currency:
            raise ToolError(f"Currency mismatch: {src.currency} -> {dst.currency} (FX not supported).")
        if amount > src.cash_balance:
            raise ToolError(f"Insufficient cash: available {src.cash_balance:,.2f} {src.currency}.")
        digest = hashlib.sha256(f"{from_portfolio_id}|{to_portfolio_id}|{amount}|{reference}".encode())
        return TransferResult(
            transfer_id=f"SIM-{digest.hexdigest()[:12].upper()}",
            status="simulated",
            from_portfolio_id=from_portfolio_id,
            to_portfolio_id=to_portfolio_id,
            amount=round(amount, 2),
            currency=src.currency,
            reference=reference,
            from_cash_before=src.cash_balance,
            from_cash_after=round(src.cash_balance - amount, 2),
            to_cash_before=dst.cash_balance,
            to_cash_after=round(dst.cash_balance + amount, 2),
            message="Simulation only: no money was moved and no data was changed.",
        )


def build_server(dataset: WealthDataset | None = None) -> MCPServer:
    """Create the wealth MCP server with its 6 tools registered."""
    service = WealthService(dataset or generate_dataset())
    mcp = MCPServer(
        name="wealth",
        title="Wealth Management Demo",
        version="0.1.0",
        instructions="Fictional wealth-management data (Faker, seed 42). All values as of "
        f"{AS_OF.isoformat()}. transfer_funds is simulated only.",
    )
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)

    @mcp.tool(annotations=read_only)
    def get_client(client_id: str | None = None, name: str | None = None) -> Client:
        """Get a client's full profile (contact, KYC, risk profile, advisor, portfolio ids).

        Look up by client_id (e.g. "CL-0001") or by a unique part of the client's name.
        """
        return service.get_client(client_id=client_id, name=name)

    @mcp.tool(annotations=read_only)
    def list_portfolios(client_id: str) -> PortfolioList:
        """List all portfolios of a client with values, strategy, fees and allocation."""
        return service.list_portfolios(client_id)

    @mcp.tool(annotations=read_only)
    def get_holdings(portfolio_id: str) -> HoldingsResult:
        """Get all positions of a portfolio (e.g. "PF-00001"), largest first."""
        return service.get_holdings(portfolio_id)

    @mcp.tool(annotations=read_only)
    def get_transactions(
        portfolio_id: str,
        start_date: date | None = None,
        end_date: date | None = None,
        tx_type: TxType | None = None,
        limit: int = 50,
    ) -> TransactionsResult:
        """Get a portfolio's transactions, newest first.

        Optional filters: start_date/end_date (YYYY-MM-DD, trade date), tx_type
        (buy, sell, dividend, interest, fee, deposit, withdrawal), limit (1-500).
        """
        return service.get_transactions(portfolio_id, start_date, end_date, tx_type, limit)

    @mcp.tool(annotations=read_only)
    def get_portfolio_performance(portfolio_id: str, period: Period = "1Y") -> PerformanceResult:
        """Get a portfolio's performance vs its benchmark over 1M, 3M, 6M, YTD or 1Y.

        Includes monthly returns, volatility, max drawdown, Sharpe ratio and top/bottom contributors.
        """
        return service.get_portfolio_performance(portfolio_id, period)

    @mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False))
    def transfer_funds(
        from_portfolio_id: str, to_portfolio_id: str, amount: float, reference: str = ""
    ) -> TransferResult:
        """HIGH RISK: transfer cash between two portfolios of the same currency.

        In this demo the transfer is SIMULATED: it validates the request and returns the
        balances it would produce, but never moves money or changes data.
        """
        return service.transfer_funds(from_portfolio_id, to_portfolio_id, amount, reference)

    return mcp
