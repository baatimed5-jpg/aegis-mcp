"""Pydantic models for the fictional wealth-management domain.

The records are intentionally wide (20-40 fields, nested objects) so that context
optimization in the gateway has a measurable effect. All data is fictional.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

RiskProfile = Literal["conservative", "moderate", "balanced", "growth", "aggressive"]
KycStatus = Literal["verified", "pending_review", "expired"]
AssetClass = Literal["equity", "bond", "etf", "fund", "commodity", "cash_equivalent"]
TxType = Literal["buy", "sell", "dividend", "interest", "fee", "deposit", "withdrawal"]
TxStatus = Literal["settled", "pending", "cancelled"]
Period = Literal["1M", "3M", "6M", "YTD", "1Y"]


class Address(BaseModel):
    """Postal address."""

    street: str
    city: str
    state: str
    postal_code: str
    country: str


class Advisor(BaseModel):
    """A wealth advisor who manages clients."""

    advisor_id: str
    name: str
    email: str
    phone: str
    office: str
    license_no: str
    title: str
    years_experience: int
    languages: list[str]


class KycInfo(BaseModel):
    """Know-your-customer compliance state."""

    status: KycStatus
    last_review: date
    next_review: date
    documents: list[str]
    pep: bool = Field(description="Politically exposed person flag")
    source_of_wealth: str


class ClientPreferences(BaseModel):
    """Communication and investment preferences."""

    contact_channel: Literal["email", "phone", "mail"]
    statement_frequency: Literal["monthly", "quarterly"]
    esg_focus: bool
    excluded_sectors: list[str]
    language: str


class Client(BaseModel):
    """A private-banking client."""

    client_id: str
    name: str
    first_name: str
    last_name: str
    birth_date: date
    email: str
    phone: str
    address: Address
    iban: str
    tax_id: str
    nationality: str
    occupation: str
    employer: str
    marital_status: Literal["single", "married", "divorced", "widowed"]
    dependents: int
    risk_profile: RiskProfile
    investment_horizon_years: int
    annual_income: float
    net_worth: float
    liquid_assets: float
    kyc: KycInfo
    preferences: ClientPreferences
    onboarding_date: date
    segment: Literal["affluent", "hnw", "uhnw"]
    advisor: Advisor
    portfolio_ids: list[str]
    notes: str
    created_at: datetime
    updated_at: datetime


class PortfolioFees(BaseModel):
    """Fee schedule of a portfolio."""

    management_fee_pct: float
    performance_fee_pct: float
    custody_fee_pct: float


class Portfolio(BaseModel):
    """A portfolio owned by a client (summary, without positions)."""

    portfolio_id: str
    client_id: str
    name: str
    account_number: str
    currency: str
    strategy: str
    mandate: Literal["discretionary", "advisory", "execution_only"]
    benchmark: str
    inception_date: date
    market_value: float
    cash_balance: float
    invested_value: float
    unrealized_pnl: float
    number_of_holdings: int
    target_allocation: dict[str, float]
    fees: PortfolioFees
    custodian: str
    status: Literal["active", "frozen", "closing"]
    last_rebalanced: date
    tax_wrapper: str
    created_at: datetime
    updated_at: datetime


class Instrument(BaseModel):
    """A tradable security (fictional issuer)."""

    isin: str
    ticker: str
    name: str
    asset_class: AssetClass
    sector: str
    country: str
    currency: str
    exchange: str
    rating: str
    issuer: str


class Holding(BaseModel):
    """A position of a portfolio in one instrument."""

    holding_id: str
    portfolio_id: str
    isin: str
    ticker: str
    name: str
    asset_class: AssetClass
    sector: str
    country: str
    currency: str
    quantity: float
    avg_cost: float
    price: float
    price_date: date
    market_value: float
    cost_basis: float
    weight: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    day_change_pct: float
    dividend_yield_pct: float
    annual_income: float
    beta: float
    volatility_pct: float
    instrument: Instrument
    acquired_on: date
    lot_count: int
    custodian_ref: str
    last_updated: datetime


class Settlement(BaseModel):
    """Settlement details of a transaction."""

    settle_date: date
    method: Literal["DVP", "FOP", "CASH"]
    clearing_house: str
    reference: str


class AuditInfo(BaseModel):
    """Who created a transaction and from which system."""

    created_by: str
    created_at: datetime
    source_system: str


class Transaction(BaseModel):
    """A booked transaction on a portfolio."""

    tx_id: str
    portfolio_id: str
    trade_date: date
    settle_date: date
    type: TxType
    isin: str | None
    ticker: str | None
    instrument_name: str | None
    quantity: float
    price: float
    gross_amount: float
    fees: float
    taxes: float
    net_amount: float
    currency: str
    fx_rate: float
    status: TxStatus
    counterparty: str
    broker: str
    order_id: str
    settlement: Settlement
    audit: AuditInfo
    comment: str


class PortfolioList(BaseModel):
    """Result of list_portfolios."""

    client_id: str
    client_name: str
    total_market_value: float
    portfolios: list[Portfolio]


class HoldingsResult(BaseModel):
    """Result of get_holdings."""

    portfolio_id: str
    as_of: date
    currency: str
    total_market_value: float
    holdings: list[Holding]


class TransactionsResult(BaseModel):
    """Result of get_transactions."""

    portfolio_id: str
    start_date: date | None
    end_date: date | None
    total_matching: int
    returned: int
    transactions: list[Transaction]


class MonthlyReturn(BaseModel):
    """One month of portfolio vs benchmark performance."""

    month: str
    portfolio_return_pct: float
    benchmark_return_pct: float
    end_value: float


class Contributor(BaseModel):
    """Contribution of one holding to performance."""

    isin: str
    name: str
    contribution_pct: float


class PerformanceResult(BaseModel):
    """Result of get_portfolio_performance."""

    portfolio_id: str
    period: Period
    as_of: date
    currency: str
    start_value: float
    end_value: float
    return_pct: float
    benchmark: str
    benchmark_return_pct: float
    excess_return_pct: float
    volatility_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    monthly: list[MonthlyReturn]
    top_contributors: list[Contributor]
    bottom_contributors: list[Contributor]


class TransferResult(BaseModel):
    """Result of a SIMULATED funds transfer. No state is changed."""

    transfer_id: str
    status: Literal["simulated"]
    from_portfolio_id: str
    to_portfolio_id: str
    amount: float
    currency: str
    reference: str
    from_cash_before: float
    from_cash_after: float
    to_cash_before: float
    to_cash_after: float
    message: str
