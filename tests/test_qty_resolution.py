from datetime import date

import pandas as pd

from tam.backtest.harness import BacktestHarness
from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.schema import OHLCV_COLUMNS
from tam.data.storage import CsvStore
from tam.portfolio.orders import Order, Qty, QtyBasis, Side
from tam.portfolio.portfolio import Portfolio, Position
from tam.strategy.base import Strategy
from tam.strategy.buy_and_hold import BuyAndHoldStrategy


class FakeProvider(DataProvider):
    def __init__(self, frame: pd.DataFrame):
        self._frame = frame

    def fetch_eod(self, symbol, start, end):
        df = self._frame
        return df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]


def _bars(dates, closes):
    index = pd.to_datetime(dates)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "adj_close": closes,
            "volume": [1_000] * len(closes),
        },
        index=index,
    ).rename_axis("date")[OHLCV_COLUMNS]


class _OneShotStrategy(Strategy):
    """Fires a single order (whatever spec.order_qty says) on the first EOD event."""

    def __init__(self, ticker, side, qty, portfolio_id):
        super().__init__()
        self._ticker = ticker
        self._side = side
        self._qty = qty
        self._portfolio_id = portfolio_id
        self._fired = False

    def state_change(self, state):
        from tam.events.clock import EOD_TOPIC
        from tam.events.types import State

        if state is State.RUNNING:
            self.subscribe_to(EOD_TOPIC)

    def on_event(self, event):
        if self._fired:
            return
        self.trade.stocks([Order(ticker=self._ticker, side=self._side, qty=self._qty, portfolio=self._portfolio_id)])
        self._fired = True


def _run(repo, strategy, portfolio, dates):
    portfolios = {portfolio.id: portfolio}
    harness = BacktestHarness(repo, [strategy], portfolios, dates)
    harness.run()
    return portfolio


def test_static_qty_is_used_directly(tmp_path):
    dates = [date(2024, 1, 2)]
    store = CsvStore(tmp_path)
    repo = DataRepository(FakeProvider(_bars(dates, [100.0])), store)
    repo.ingest(["AAPL"], dates[0], dates[0])

    portfolio = Portfolio("main", cash=10_000.0)
    strategy = _OneShotStrategy("AAPL", Side.BUY, 7, "main")

    _run(repo, strategy, portfolio, dates)

    assert portfolio.position("AAPL").qty == 7
    assert portfolio.cash == 10_000.0 - 7 * 100.0


def test_buy_pct_of_cash_is_the_default_basis(tmp_path):
    dates = [date(2024, 1, 2)]
    store = CsvStore(tmp_path)
    repo = DataRepository(FakeProvider(_bars(dates, [100.0])), store)
    repo.ingest(["AAPL"], dates[0], dates[0])

    portfolio = Portfolio("main", cash=1_000.0)
    strategy = _OneShotStrategy("AAPL", Side.BUY, Qty(pct=20), "main")  # default basis = cash

    _run(repo, strategy, portfolio, dates)

    # 20% of $1000 cash = $200 budget -> 2 shares @ $100.
    assert portfolio.position("AAPL").qty == 2
    assert portfolio.cash == 800.0


def test_buy_pct_of_portfolio_value_includes_existing_holdings(tmp_path):
    dates = [date(2024, 1, 2)]
    prices = {"AAPL": 100.0, "MSFT": 50.0}

    class TwoTickerProvider(DataProvider):
        def fetch_eod(self, symbol, start, end):
            return _bars(dates, [prices[symbol]])

    store = CsvStore(tmp_path)
    repo = DataRepository(TwoTickerProvider(), store)
    repo.ingest(["AAPL", "MSFT"], dates[0], dates[0])

    # Total portfolio value = 500 cash + 10 MSFT @ $50 = 1000. 20% of that = $200 -> 2 AAPL @ $100.
    portfolio = Portfolio("main", cash=500.0)
    portfolio._positions["MSFT"] = Position(ticker="MSFT", qty=10, avg_price=50.0)

    strategy = _OneShotStrategy("AAPL", Side.BUY, Qty(pct=20, basis=QtyBasis.PORTFOLIO_VALUE), "main")

    _run(repo, strategy, portfolio, dates)

    assert portfolio.position("AAPL").qty == 2


def test_sell_pct_100_sells_the_entire_position(tmp_path):
    dates = [date(2024, 1, 2)]
    store = CsvStore(tmp_path)
    repo = DataRepository(FakeProvider(_bars(dates, [100.0])), store)
    repo.ingest(["AAPL"], dates[0], dates[0])

    portfolio = Portfolio("main", cash=1_000.0)
    portfolio.execute(
        Order(ticker="AAPL", side=Side.BUY, qty=10, portfolio="main"), qty=10, price=100.0, as_of=dates[0]
    )

    strategy = _OneShotStrategy("AAPL", Side.SELL, Qty(pct=100), "main")
    _run(repo, strategy, portfolio, dates)

    assert portfolio.position("AAPL").qty == 0
    assert portfolio.cash == 1_000.0


def test_sell_pct_partial_sells_a_fraction_of_the_position(tmp_path):
    dates = [date(2024, 1, 2)]
    store = CsvStore(tmp_path)
    repo = DataRepository(FakeProvider(_bars(dates, [100.0])), store)
    repo.ingest(["AAPL"], dates[0], dates[0])

    portfolio = Portfolio("main", cash=0.0)
    portfolio.execute(
        Order(ticker="AAPL", side=Side.BUY, qty=10, portfolio="main"), qty=10, price=100.0, as_of=dates[0]
    )

    strategy = _OneShotStrategy("AAPL", Side.SELL, Qty(pct=50), "main")
    _run(repo, strategy, portfolio, dates)

    assert portfolio.position("AAPL").qty == 5


def test_buy_and_hold_default_pct_100_invests_all_available_cash(tmp_path):
    dates = [date(2024, 1, 2)]
    store = CsvStore(tmp_path)
    repo = DataRepository(FakeProvider(_bars(dates, [37.0])), store)
    repo.ingest(["AAPL"], dates[0], dates[0])

    portfolio = Portfolio("main", cash=1_000.0)
    strategy = BuyAndHoldStrategy("AAPL", Qty(pct=100), "main")

    _run(repo, strategy, portfolio, dates)

    # floor(1000 / 37) = 27 shares.
    assert portfolio.position("AAPL").qty == 27
    assert portfolio.cash == 1_000.0 - 27 * 37.0


def test_resolved_zero_qty_executes_no_trade(tmp_path):
    dates = [date(2024, 1, 2)]
    store = CsvStore(tmp_path)
    repo = DataRepository(FakeProvider(_bars(dates, [10_000.0])), store)
    repo.ingest(["AAPL"], dates[0], dates[0])

    portfolio = Portfolio("main", cash=1.0)  # 1% of $1 is nowhere near one $10,000 share
    strategy = _OneShotStrategy("AAPL", Side.BUY, Qty(pct=1), "main")

    _run(repo, strategy, portfolio, dates)

    assert portfolio.trades == []
    assert portfolio.cash == 1.0
