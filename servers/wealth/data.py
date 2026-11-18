"""Deterministic generator for the fictional wealth-management dataset.

All data is generated in memory at startup from a fixed seed (decision D9), so two runs
with the same seed and sizes produce exactly the same records. Values are expressed in
the portfolio currency (no FX conversion), and every name, IBAN or tax id is fake.
"""

from __future__ import annotations

import math
import random
import statistics
import string
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from faker import Faker

from servers.wealth.models import (
    Address,
    Advisor,
    AssetClass,
    AuditInfo,
    Client,
    ClientPreferences,
    Holding,
    Instrument,
    KycInfo,
    Portfolio,
    PortfolioFees,
    RiskProfile,
    Settlement,
    Transaction,
    TxStatus,
    TxType,
)

SEED = 42
AS_OF = date(2026, 10, 30)
"""Fixed valuation date: data must not depend on the day the server starts."""
HISTORY_MONTHS = 24

_COUNTRIES = ["US", "GB", "DE", "FR", "CH", "JP", "NL", "CA", "IE", "SE"]
_EXCHANGES = {"US": "NYSE", "GB": "LSE", "DE": "XETRA", "FR": "EPA", "CH": "SIX", "JP": "TSE",
              "NL": "AEX", "CA": "TSX", "IE": "ISE", "SE": "OMX"}  # fmt: skip
_CCY = {"US": "USD", "GB": "GBP", "DE": "EUR", "FR": "EUR", "CH": "CHF", "JP": "JPY",
        "NL": "EUR", "CA": "CAD", "IE": "EUR", "SE": "SEK"}  # fmt: skip
_SECTORS = ["Technology", "Healthcare", "Financials", "Energy", "Industrials", "Consumer Staples",
            "Consumer Discretionary", "Utilities", "Materials", "Real Estate", "Communication"]  # fmt: skip
_RATINGS = ["AAA", "AA+", "AA", "A+", "A", "A-", "BBB+", "BBB", "BB+"]
_STRATEGIES = {
    "conservative": ("Capital Preservation", {"bond": 0.6, "equity": 0.15, "cash_equivalent": 0.25}),
    "moderate": ("Income", {"bond": 0.5, "equity": 0.35, "cash_equivalent": 0.15}),
    "balanced": ("Balanced", {"bond": 0.4, "equity": 0.5, "cash_equivalent": 0.1}),
    "growth": ("Growth", {"bond": 0.2, "equity": 0.7, "commodity": 0.05, "cash_equivalent": 0.05}),
    "aggressive": ("Aggressive Growth", {"equity": 0.85, "commodity": 0.1, "cash_equivalent": 0.05}),
}
# Monthly (mean, stdev) of returns per risk profile.
_RETURN_PARAMS: dict[str, tuple[float, float]] = {
    "conservative": (0.002, 0.008),
    "moderate": (0.003, 0.014),
    "balanced": (0.005, 0.022),
    "growth": (0.007, 0.032),
    "aggressive": (0.009, 0.045),
}
_BENCHMARKS = {
    "conservative": "Bloomberg Global Aggregate",
    "moderate": "60/40 Global Composite",
    "balanced": "MSCI World 50/50 Blend",
    "growth": "MSCI World",
    "aggressive": "MSCI ACWI IMI",
}
_CUSTODIANS = ["Northbridge Custody", "Helvetia Trust Services", "Atlantic Securities Services"]
_BROKERS = ["Kestrel Markets", "Ironwood Securities", "Bluefin Execution", "Harbor Prime"]
_CLEARING = ["DTCC", "Euroclear", "Clearstream", "SIX SIS"]
_SOURCE_SYSTEMS = ["OMS-Core", "Advisor-Portal", "Batch-Corporate-Actions"]


def isin_check_digit(body: str) -> int:
    """Return the ISIN check digit (Luhn over the letter-expanded 11-char body)."""
    digits = "".join(str(int(c, 36)) for c in body)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 0:  # rightmost digit is doubled
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return (10 - total % 10) % 10


def is_valid_isin(isin: str) -> bool:
    """Check the format and the check digit of an ISIN."""
    if len(isin) != 12 or not isin[:2].isalpha() or not isin[-1].isdigit():
        return False
    return isin_check_digit(isin[:11]) == int(isin[-1])


@dataclass
class WealthDataset:
    """In-memory fictional dataset with lookup indexes."""

    advisors: dict[str, Advisor] = field(default_factory=dict)
    clients: dict[str, Client] = field(default_factory=dict)
    portfolios: dict[str, Portfolio] = field(default_factory=dict)
    instruments: dict[str, Instrument] = field(default_factory=dict)
    holdings: dict[str, list[Holding]] = field(default_factory=dict)
    transactions: dict[str, list[Transaction]] = field(default_factory=dict)
    monthly_returns: dict[str, list[tuple[str, float, float]]] = field(default_factory=dict)
    """portfolio_id -> [(YYYY-MM, portfolio_return, benchmark_return)], oldest first."""
    contribution_shares: dict[str, list[tuple[str, str, float]]] = field(default_factory=dict)
    """portfolio_id -> [(isin, name, share of total return)], shares sum to 1."""


class _Generator:
    """Builds a WealthDataset from a seed. Generation order is fixed for reproducibility."""

    def __init__(self, seed: int, n_advisors: int, n_clients: int, n_instruments: int) -> None:
        self.rng = random.Random(seed)
        self.fake = Faker("en_US")
        self.fake.seed_instance(seed)
        self.n_advisors = n_advisors
        self.n_clients = n_clients
        self.n_instruments = n_instruments
        self.ds = WealthDataset()
        self._tx_counter = 0
        self._holding_counter = 0

    # ---- helpers -------------------------------------------------------------------
    def _date_between(self, start: date, end: date) -> date:
        return start + timedelta(days=self.rng.randint(0, (end - start).days))

    def _ts(self, d: date) -> datetime:
        return datetime.combine(d, time(self.rng.randint(7, 19), self.rng.randint(0, 59), tzinfo=UTC))

    def _money(self, x: float) -> float:
        return round(x, 2)

    # ---- entities ------------------------------------------------------------------
    def advisors(self) -> None:
        offices = ["New York", "Boston", "Chicago", "San Francisco", "London", "Geneva", "Zurich"]
        for i in range(1, self.n_advisors + 1):
            name = self.fake.name()
            adv = Advisor(
                advisor_id=f"ADV-{i:03d}",
                name=name,
                email=f"{name.lower().replace(' ', '.')}@aegis-wealth.example",
                phone=self.fake.phone_number(),
                office=self.rng.choice(offices),
                license_no=f"CRD-{self.rng.randint(1_000_000, 9_999_999)}",
                title=self.rng.choice(["Wealth Advisor", "Senior Wealth Advisor", "Private Banker"]),
                years_experience=self.rng.randint(3, 30),
                languages=self.rng.sample(["English", "French", "German", "Spanish", "Arabic"], 2),
            )
            self.ds.advisors[adv.advisor_id] = adv

    def instruments(self) -> None:
        classes: list[AssetClass] = ["equity", "bond", "etf", "fund", "commodity", "cash_equivalent"]
        class_weights = [0.45, 0.25, 0.12, 0.08, 0.05, 0.05]
        used_tickers: set[str] = set()
        for _ in range(self.n_instruments):
            country = self.rng.choice(_COUNTRIES)
            body = country + "".join(self.rng.choices(string.ascii_uppercase + string.digits, k=9))
            isin = body + str(isin_check_digit(body))
            issuer = self.fake.company()
            asset_class = self.rng.choices(classes, class_weights)[0]
            ticker = "".join(c for c in issuer.upper() if c.isalpha())[:4]
            while ticker in used_tickers or len(ticker) < 3:
                ticker = "".join(self.rng.choices(string.ascii_uppercase, k=4))
            used_tickers.add(ticker)
            if asset_class == "bond":
                coupon = self.rng.choice([1.5, 2.25, 3.0, 3.75, 4.25, 5.0])
                name = f"{issuer} {coupon}% {self.rng.randint(2028, 2040)}"
            elif asset_class in ("etf", "fund"):
                name = f"{issuer} {self.rng.choice(_SECTORS)} {asset_class.upper()}"
            elif asset_class == "commodity":
                name = f"{issuer} Physical {self.rng.choice(['Gold', 'Silver', 'Platinum'])}"
            elif asset_class == "cash_equivalent":
                name = f"{issuer} Treasury Money Market"
            else:
                name = f"{issuer} Inc."
            self.ds.instruments[isin] = Instrument(
                isin=isin,
                ticker=ticker,
                name=name,
                asset_class=asset_class,
                sector=self.rng.choice(_SECTORS),
                country=country,
                currency=_CCY[country],
                exchange=_EXCHANGES[country],
                rating=self.rng.choice(_RATINGS),
                issuer=issuer,
            )

    def clients(self) -> None:
        advisor_ids = list(self.ds.advisors)
        profiles: list[RiskProfile] = ["conservative", "moderate", "balanced", "growth", "aggressive"]
        pf_counter = 0
        for i in range(1, self.n_clients + 1):
            first, last = self.fake.first_name(), self.fake.last_name()
            risk = self.rng.choice(profiles)
            segment = self.rng.choices(["affluent", "hnw", "uhnw"], [0.5, 0.4, 0.1])[0]
            net_worth = {
                "affluent": self.rng.uniform(5e5, 2e6),
                "hnw": self.rng.uniform(2e6, 2.5e7),
                "uhnw": self.rng.uniform(3e7, 1.5e8),
            }[segment]
            onboarding = self._date_between(date(2012, 1, 1), date(2024, 6, 30))
            last_review = self._date_between(date(2025, 1, 1), AS_OF)
            n_portfolios = self.rng.choice([1, 1, 2, 2, 3])
            portfolio_ids = [f"PF-{pf_counter + k + 1:05d}" for k in range(n_portfolios)]
            pf_counter += n_portfolios
            client = Client(
                client_id=f"CL-{i:04d}",
                name=f"{first} {last}",
                first_name=first,
                last_name=last,
                birth_date=self._date_between(date(1945, 1, 1), date(1995, 12, 31)),
                email=f"{first.lower()}.{last.lower()}@{self.fake.free_email_domain()}",
                phone=self.fake.phone_number(),
                address=Address(
                    street=self.fake.street_address(),
                    city=self.fake.city(),
                    state=self.fake.state_abbr(),
                    postal_code=self.fake.postcode(),
                    country="US",
                ),
                iban=self.fake.iban(),
                tax_id=self.fake.ssn(),
                nationality=self.rng.choice(["US", "US", "US", "GB", "FR", "DE", "CH", "CA"]),
                occupation=self.fake.job(),
                employer=self.fake.company(),
                marital_status=self.rng.choice(["single", "married", "married", "divorced", "widowed"]),
                dependents=self.rng.randint(0, 4),
                risk_profile=risk,
                investment_horizon_years=self.rng.choice([3, 5, 7, 10, 15, 20]),
                annual_income=self._money(net_worth * self.rng.uniform(0.03, 0.12)),
                net_worth=self._money(net_worth),
                liquid_assets=self._money(net_worth * self.rng.uniform(0.4, 0.8)),
                kyc=KycInfo(
                    status=self.rng.choices(["verified", "pending_review", "expired"], [0.85, 0.1, 0.05])[0],
                    last_review=last_review,
                    next_review=last_review + timedelta(days=365),
                    documents=self.rng.sample(
                        ["passport", "driver_license", "utility_bill", "bank_statement", "tax_return"], 3
                    ),
                    pep=self.rng.random() < 0.05,
                    source_of_wealth=self.rng.choice(
                        ["employment", "business_sale", "inheritance", "investments", "real_estate"]
                    ),
                ),
                preferences=ClientPreferences(
                    contact_channel=self.rng.choice(["email", "phone", "mail"]),
                    statement_frequency=self.rng.choice(["monthly", "quarterly"]),
                    esg_focus=self.rng.random() < 0.3,
                    excluded_sectors=self.rng.sample(["Energy", "Materials", "Utilities", "Financials"], 1)
                    if self.rng.random() < 0.3
                    else [],
                    language=self.rng.choice(["en", "en", "fr", "de", "es"]),
                ),
                onboarding_date=onboarding,
                segment=segment,
                advisor=self.ds.advisors[self.rng.choice(advisor_ids)],
                portfolio_ids=portfolio_ids,
                notes=self.fake.sentence(nb_words=14),
                created_at=self._ts(onboarding),
                updated_at=self._ts(last_review),
            )
            self.ds.clients[client.client_id] = client
            for pid in portfolio_ids:
                self.portfolio(client, pid)

    def portfolio(self, client: Client, portfolio_id: str) -> None:
        strategy, target = _STRATEGIES[client.risk_profile]
        currency = self.rng.choices(["USD", "EUR", "GBP", "CHF"], [0.7, 0.15, 0.1, 0.05])[0]
        invested_target = client.liquid_assets / len(client.portfolio_ids) * self.rng.uniform(0.85, 0.95)
        cash = self._money(invested_target * self.rng.uniform(0.02, 0.08))
        inception = self._date_between(client.onboarding_date, date(2024, 10, 1))

        # Holdings: pick instruments, spread value with random weights.
        isins = self.rng.sample(list(self.ds.instruments), self.rng.randint(12, 25))
        raw = [self.rng.uniform(0.5, 3.0) for _ in isins]
        total_raw = sum(raw)
        holdings: list[Holding] = []
        for isin, w in zip(isins, raw, strict=True):
            inst = self.ds.instruments[isin]
            is_bond = inst.asset_class == "bond"
            price = round(self.rng.uniform(90, 110) if is_bond else self.rng.uniform(8, 650), 2)
            quantity = round(invested_target * w / total_raw / price, 2)
            avg_cost = round(price * self.rng.uniform(0.7, 1.2), 2)
            mv = self._money(quantity * price)
            cost_basis = self._money(quantity * avg_cost)
            div_yield = round(self.rng.uniform(0, 5.5), 2)
            self._holding_counter += 1
            acquired = self._date_between(inception, AS_OF - timedelta(days=30))
            holdings.append(
                Holding(
                    holding_id=f"H-{self._holding_counter:06d}",
                    portfolio_id=portfolio_id,
                    isin=isin,
                    ticker=inst.ticker,
                    name=inst.name,
                    asset_class=inst.asset_class,
                    sector=inst.sector,
                    country=inst.country,
                    currency=currency,
                    quantity=quantity,
                    avg_cost=avg_cost,
                    price=price,
                    price_date=AS_OF,
                    market_value=mv,
                    cost_basis=cost_basis,
                    weight=0.0,  # filled below once the total is known
                    unrealized_pnl=self._money(mv - cost_basis),
                    unrealized_pnl_pct=round((mv - cost_basis) / cost_basis * 100, 2) if cost_basis else 0.0,
                    day_change_pct=round(self.rng.gauss(0, 1.2), 2),
                    dividend_yield_pct=div_yield,
                    annual_income=self._money(mv * div_yield / 100),
                    beta=round(self.rng.uniform(0.1, 1.6), 2),
                    volatility_pct=round(self.rng.uniform(2, 45), 2),
                    instrument=inst,
                    acquired_on=acquired,
                    lot_count=self.rng.randint(1, 6),
                    custodian_ref=f"CUS-{self.rng.randint(10**7, 10**8 - 1)}",
                    last_updated=self._ts(AS_OF),
                )
            )
        invested = sum(h.market_value for h in holdings)
        for h in holdings:
            h.weight = round(h.market_value / (invested + cash) * 100, 2)
        self.ds.holdings[portfolio_id] = holdings
        unrealized = sum(h.unrealized_pnl for h in holdings)

        self.ds.portfolios[portfolio_id] = Portfolio(
            portfolio_id=portfolio_id,
            client_id=client.client_id,
            name=f"{client.last_name} {strategy}",
            account_number=f"{self.rng.randint(10**9, 10**10 - 1)}",
            currency=currency,
            strategy=strategy,
            mandate=self.rng.choice(["discretionary", "advisory", "execution_only"]),
            benchmark=_BENCHMARKS[client.risk_profile],
            inception_date=inception,
            market_value=self._money(invested + cash),
            cash_balance=cash,
            invested_value=self._money(invested),
            unrealized_pnl=self._money(unrealized),
            number_of_holdings=len(holdings),
            target_allocation=target,
            fees=PortfolioFees(
                management_fee_pct=self.rng.choice([0.5, 0.75, 1.0, 1.25]),
                performance_fee_pct=self.rng.choice([0.0, 0.0, 10.0, 15.0]),
                custody_fee_pct=self.rng.choice([0.05, 0.1, 0.15]),
            ),
            custodian=self.rng.choice(_CUSTODIANS),
            status=self.rng.choices(["active", "frozen", "closing"], [0.92, 0.05, 0.03])[0],
            last_rebalanced=self._date_between(date(2026, 1, 1), AS_OF),
            tax_wrapper=self.rng.choice(["Taxable", "IRA", "Roth IRA", "Trust", "Corporate"]),
            created_at=self._ts(inception),
            updated_at=self._ts(AS_OF),
        )
        self.transactions(portfolio_id, holdings, currency)
        self.performance(portfolio_id, client.risk_profile, holdings)

    def transactions(self, portfolio_id: str, holdings: list[Holding], currency: str) -> None:
        tx_types: list[TxType] = ["buy", "sell", "dividend", "interest", "fee", "deposit", "withdrawal"]
        tx_weights = [0.35, 0.2, 0.15, 0.08, 0.1, 0.07, 0.05]
        start = AS_OF - timedelta(days=730)
        txs: list[Transaction] = []
        for _ in range(self.rng.randint(40, 90)):
            self._tx_counter += 1
            tx_type = self.rng.choices(tx_types, tx_weights)[0]
            trade = self._date_between(start, AS_OF)
            settle = trade + timedelta(days=2 if tx_type in ("buy", "sell") else 0)
            h = self.rng.choice(holdings) if tx_type in ("buy", "sell", "dividend") else None
            if tx_type in ("buy", "sell") and h is not None:
                qty = round(h.quantity * self.rng.uniform(0.05, 0.3), 2)
                price = round(h.price * self.rng.uniform(0.85, 1.1), 2)
                gross = qty * price
            else:
                qty, price = 0.0, 0.0
                gross = {
                    "dividend": self.rng.uniform(50, 8_000),
                    "interest": self.rng.uniform(10, 2_000),
                    "fee": self.rng.uniform(100, 5_000),
                    "deposit": self.rng.uniform(10_000, 500_000),
                    "withdrawal": self.rng.uniform(5_000, 200_000),
                }.get(tx_type, 0.0)
            fees = round(gross * 0.001, 2) if tx_type in ("buy", "sell") else 0.0
            taxes = round(gross * 0.15, 2) if tx_type == "dividend" else 0.0
            sign = -1 if tx_type in ("buy", "fee", "withdrawal") else 1
            net = sign * gross - fees - taxes
            final_states: list[TxStatus] = ["settled", "cancelled"]
            status: TxStatus = (
                "pending" if settle > AS_OF else self.rng.choices(final_states, [0.97, 0.03])[0]
            )
            txs.append(
                Transaction(
                    tx_id=f"TX-{self._tx_counter:07d}",
                    portfolio_id=portfolio_id,
                    trade_date=trade,
                    settle_date=settle,
                    type=tx_type,
                    isin=h.isin if h else None,
                    ticker=h.ticker if h else None,
                    instrument_name=h.name if h else None,
                    quantity=qty,
                    price=price,
                    gross_amount=self._money(gross),
                    fees=fees,
                    taxes=taxes,
                    net_amount=self._money(net),
                    currency=currency,
                    fx_rate=1.0,
                    status=status,
                    counterparty=self.rng.choice(_BROKERS) if h else "Internal",
                    broker=self.rng.choice(_BROKERS),
                    order_id=f"ORD-{self.rng.randint(10**8, 10**9 - 1)}",
                    settlement=Settlement(
                        settle_date=settle,
                        method="DVP" if tx_type in ("buy", "sell") else "CASH",
                        clearing_house=self.rng.choice(_CLEARING),
                        reference=f"STL-{self.rng.randint(10**9, 10**10 - 1)}",
                    ),
                    audit=AuditInfo(
                        created_by=self.rng.choice(["system", "advisor", "ops-desk"]),
                        created_at=self._ts(trade),
                        source_system=self.rng.choice(_SOURCE_SYSTEMS),
                    ),
                    comment=self.fake.sentence(nb_words=8),
                )
            )
        txs.sort(key=lambda t: (t.trade_date, t.tx_id), reverse=True)
        self.ds.transactions[portfolio_id] = txs

    def performance(self, portfolio_id: str, risk: str, holdings: list[Holding]) -> None:
        mean, sd = _RETURN_PARAMS[risk]
        series: list[tuple[str, float, float]] = []
        y, m = AS_OF.year, AS_OF.month
        months: list[str] = []
        for _ in range(HISTORY_MONTHS):
            months.append(f"{y:04d}-{m:02d}")
            y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        for month in reversed(months):
            b = self.rng.gauss(mean, sd)
            p = b + self.rng.gauss(0, sd * 0.35)
            series.append((month, p, b))
        self.ds.monthly_returns[portfolio_id] = series
        raw = [h.weight * (1 + self.rng.gauss(0, 2.0)) for h in holdings]
        total = sum(raw) or 1.0
        self.ds.contribution_shares[portfolio_id] = [
            (h.isin, h.name, r / total) for h, r in zip(holdings, raw, strict=True)
        ]

    def build(self) -> WealthDataset:
        self.advisors()
        self.instruments()
        self.clients()
        return self.ds


def generate_dataset(
    seed: int = SEED, n_advisors: int = 8, n_clients: int = 40, n_instruments: int = 80
) -> WealthDataset:
    """Generate the full fictional dataset deterministically from ``seed``."""
    return _Generator(seed, n_advisors, n_clients, n_instruments).build()


def annualized_volatility(monthly: list[float]) -> float:
    """Annualized volatility (in %) of monthly returns given as fractions."""
    if len(monthly) < 2:
        return 0.0
    return statistics.stdev(monthly) * math.sqrt(12) * 100


def max_drawdown(monthly: list[float]) -> float:
    """Maximum drawdown (in %, <= 0) of a series of monthly returns."""
    peak, value, worst = 1.0, 1.0, 0.0
    for r in monthly:
        value *= 1 + r
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst * 100
