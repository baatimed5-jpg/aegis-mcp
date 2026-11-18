"""Unit tests for the deterministic wealth data generator."""

from __future__ import annotations

import pytest

from servers.wealth.data import (
    AS_OF,
    WealthDataset,
    generate_dataset,
    is_valid_isin,
    isin_check_digit,
    max_drawdown,
)


@pytest.fixture(scope="module")
def ds() -> WealthDataset:
    return generate_dataset()


def test_isin_check_digit_known_value() -> None:
    # Apple Inc. ISIN US0378331005 is a public, well-known example.
    assert isin_check_digit("US037833100") == 5
    assert is_valid_isin("US0378331005")
    assert not is_valid_isin("US0378331006")


def test_generation_is_deterministic(ds: WealthDataset) -> None:
    again = generate_dataset()
    assert again.clients["CL-0001"] == ds.clients["CL-0001"]
    assert again.holdings["PF-00001"] == ds.holdings["PF-00001"]
    assert again.transactions["PF-00002"] == ds.transactions["PF-00002"]


def test_other_seed_gives_other_data(ds: WealthDataset) -> None:
    assert generate_dataset(seed=7).clients["CL-0001"].name != ds.clients["CL-0001"].name


def test_sizes_and_links(ds: WealthDataset) -> None:
    assert len(ds.clients) == 40
    for client in ds.clients.values():
        assert client.advisor.advisor_id in ds.advisors
        for pid in client.portfolio_ids:
            assert ds.portfolios[pid].client_id == client.client_id
            assert 12 <= len(ds.holdings[pid]) <= 25
            assert 40 <= len(ds.transactions[pid]) <= 90


def test_all_isins_valid(ds: WealthDataset) -> None:
    assert all(is_valid_isin(i) for i in ds.instruments)


def test_portfolio_values_are_consistent(ds: WealthDataset) -> None:
    for pid, pf in ds.portfolios.items():
        invested = sum(h.market_value for h in ds.holdings[pid])
        assert pf.invested_value == pytest.approx(invested, abs=0.05)
        assert pf.market_value == pytest.approx(invested + pf.cash_balance, abs=0.05)
        assert pf.number_of_holdings == len(ds.holdings[pid])
        weights = sum(h.weight for h in ds.holdings[pid])
        assert weights == pytest.approx(100 * invested / pf.market_value, abs=0.2)


def test_records_are_wide(ds: WealthDataset) -> None:
    """Records must have many fields so that pruning matters (spec section 5)."""
    assert len(type(ds.clients["CL-0001"]).model_fields) >= 25
    assert len(type(ds.holdings["PF-00001"][0]).model_fields) >= 25
    assert len(type(ds.transactions["PF-00001"][0]).model_fields) >= 20


def test_transactions_sorted_and_not_after_as_of(ds: WealthDataset) -> None:
    for txs in ds.transactions.values():
        dates = [t.trade_date for t in txs]
        assert dates == sorted(dates, reverse=True)
        assert all(d <= AS_OF for d in dates)
        assert all(t.status == "pending" for t in txs if t.settle_date > AS_OF)


def test_contribution_shares_sum_to_one(ds: WealthDataset) -> None:
    for shares in ds.contribution_shares.values():
        assert sum(s for _, _, s in shares) == pytest.approx(1.0)


def test_max_drawdown() -> None:
    assert max_drawdown([0.1, -0.5, 0.2]) == pytest.approx(-50.0)
    assert max_drawdown([0.01, 0.02]) == 0.0
