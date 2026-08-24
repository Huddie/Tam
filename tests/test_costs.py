from datetime import date

import pytest

from tam.portfolio.costs import BpsCost, CostModel, ZeroCost
from tam.portfolio.orders import Order, Side
from tam.portfolio.portfolio import Portfolio
from tam.registry import Registry


def test_zero_cost_is_the_default_and_changes_nothing():
    portfolio = Portfolio("main", cash=10_000.0)

    portfolio.execute(Order(ticker="AAPL", side=Side.BUY, qty=10, portfolio="main"), qty=10, price=100.0, as_of=date(2024, 1, 1))

    assert portfolio.cash == 9_000.0


def test_bps_cost_reduces_cash_on_a_buy():
    portfolio = Portfolio("main", cash=10_000.0, cost_model=BpsCost(rate=0.0005))

    portfolio.execute(Order(ticker="AAPL", side=Side.BUY, qty=10, portfolio="main"), qty=10, price=100.0, as_of=date(2024, 1, 1))

    # 10 * 100 = 1000 notional -> cash -1000 for the trade, -0.5 for 5bps cost.
    assert portfolio.cash == pytest.approx(9_000.0 - 0.5)


def test_bps_cost_reduces_cash_on_a_sell_too():
    portfolio = Portfolio("main", cash=0.0, cost_model=BpsCost(rate=0.0005))
    portfolio.execute(Order(ticker="AAPL", side=Side.BUY, qty=10, portfolio="main"), qty=10, price=100.0, as_of=date(2024, 1, 1))
    cash_after_buy = portfolio.cash

    portfolio.execute(Order(ticker="AAPL", side=Side.SELL, qty=10, portfolio="main"), qty=10, price=100.0, as_of=date(2024, 1, 2))

    # +1000 notional from the sale, -0.5 cost -- a cost on BOTH legs of a round trip.
    assert portfolio.cash == pytest.approx(cash_after_buy + 1_000.0 - 0.5)


def test_a_full_round_trip_costs_twice_the_flat_rate():
    zero = Portfolio("main", cash=10_000.0)
    priced = Portfolio("main", cash=10_000.0, cost_model=BpsCost(rate=0.0005))

    for portfolio in (zero, priced):
        portfolio.execute(Order(ticker="AAPL", side=Side.BUY, qty=10, portfolio="main"), qty=10, price=100.0, as_of=date(2024, 1, 1))
        portfolio.execute(Order(ticker="AAPL", side=Side.SELL, qty=10, portfolio="main"), qty=10, price=100.0, as_of=date(2024, 1, 2))

    assert zero.cash == 10_000.0  # flat price, no cost -> unchanged
    assert priced.cash == pytest.approx(10_000.0 - 2 * 0.5)  # cost on both legs


def test_builtin_cost_models_are_registered():
    assert set(Registry.names(CostModel)) >= {"zero", "bps"}
