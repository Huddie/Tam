from datetime import date

from tam.portfolio.orders import Order, Side
from tam.portfolio.portfolio import Portfolio


def test_buy_reduces_cash_and_opens_position():
    portfolio = Portfolio("p1", cash=10_000.0)
    order = Order(ticker="AAPL", side=Side.BUY, qty=10, portfolio="p1")

    portfolio.execute(order, qty=10, price=100.0, as_of=date(2024, 1, 2))

    assert portfolio.cash == 9_000.0
    position = portfolio.position("AAPL")
    assert position.qty == 10
    assert position.avg_price == 100.0


def test_sell_increases_cash_and_reduces_position():
    portfolio = Portfolio("p1", cash=10_000.0)
    portfolio.execute(
        Order(ticker="AAPL", side=Side.BUY, qty=10, portfolio="p1"), qty=10, price=100.0, as_of=date(2024, 1, 2)
    )
    portfolio.execute(
        Order(ticker="AAPL", side=Side.SELL, qty=4, portfolio="p1"), qty=4, price=110.0, as_of=date(2024, 1, 3)
    )

    assert portfolio.cash == 10_000.0 - 1_000.0 + 440.0
    assert portfolio.position("AAPL").qty == 6


def test_avg_price_weighted_across_buys():
    portfolio = Portfolio("p1", cash=10_000.0)
    portfolio.execute(
        Order(ticker="AAPL", side=Side.BUY, qty=10, portfolio="p1"), qty=10, price=100.0, as_of=date(2024, 1, 2)
    )
    portfolio.execute(
        Order(ticker="AAPL", side=Side.BUY, qty=10, portfolio="p1"), qty=10, price=120.0, as_of=date(2024, 1, 3)
    )

    assert portfolio.position("AAPL").avg_price == 110.0


def test_market_value_combines_cash_and_holdings():
    portfolio = Portfolio("p1", cash=10_000.0)
    portfolio.execute(
        Order(ticker="AAPL", side=Side.BUY, qty=10, portfolio="p1"), qty=10, price=100.0, as_of=date(2024, 1, 2)
    )

    value = portfolio.market_value({"AAPL": 105.0})
    assert value == 9_000.0 + 1_050.0


def test_trades_are_recorded_in_order():
    portfolio = Portfolio("p1", cash=10_000.0)
    portfolio.execute(
        Order(ticker="AAPL", side=Side.BUY, qty=10, portfolio="p1"), qty=10, price=100.0, as_of=date(2024, 1, 2)
    )
    portfolio.execute(
        Order(ticker="AAPL", side=Side.SELL, qty=5, portfolio="p1"), qty=5, price=110.0, as_of=date(2024, 1, 3)
    )

    assert [t.qty for t in portfolio.trades] == [10, 5]
    assert [t.side for t in portfolio.trades] == [Side.BUY, Side.SELL]
