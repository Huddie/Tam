from datetime import date, timedelta

import pandas as pd

from tam.backtest.harness import BacktestHarness
from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.schema import OHLCV_COLUMNS
from tam.data.storage import CsvStore
from tam.portfolio.orders import Side
from tam.portfolio.portfolio import Portfolio
from tam.strategy.ma_crossover import MACrossoverStrategy


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


def test_ma_crossover_buys_when_first_drops_below_second_and_sells_on_reversal(tmp_path):
    # first=SMA(3), second=SMA(2): a flat-up-flat-down-flat price shape gives one
    # clean buy (first < second) followed by one clean sell (first > second).
    closes = [10, 10, 10, 20, 20, 20, 5, 5, 5]
    start_day = date(2024, 1, 2)
    dates = [start_day + timedelta(days=i) for i in range(len(closes))]

    store = CsvStore(tmp_path)
    repo = DataRepository(FakeProvider(_bars(dates, [float(c) for c in closes])), store)
    repo.ingest(["AAPL"], dates[0], dates[-1])

    strategy = MACrossoverStrategy(
        repo, "AAPL", first_window=3, second_window=2, buy_qty=10, sell_qty=10, portfolio_id="main"
    )
    portfolios = {"main": Portfolio("main", cash=10_000.0)}

    harness = BacktestHarness(repo, [strategy], portfolios, dates)
    harness.run()

    trades = portfolios["main"].trades
    assert [t.side for t in trades] == [Side.BUY, Side.SELL]
    assert [t.date for t in trades] == [dates[3], dates[6]]
    assert [t.qty for t in trades] == [10, 10]


def test_ma_crossover_requires_full_lookback_before_trading(tmp_path):
    closes = [10.0, 20.0]
    dates = [date(2024, 1, 2), date(2024, 1, 3)]

    store = CsvStore(tmp_path)
    repo = DataRepository(FakeProvider(_bars(dates, closes)), store)
    repo.ingest(["AAPL"], dates[0], dates[-1])

    strategy = MACrossoverStrategy(
        repo, "AAPL", first_window=3, second_window=2, buy_qty=10, sell_qty=10, portfolio_id="main"
    )
    portfolios = {"main": Portfolio("main", cash=10_000.0)}

    harness = BacktestHarness(repo, [strategy], portfolios, dates)
    harness.run()

    assert portfolios["main"].trades == []


def test_ma_crossover_works_regardless_of_which_window_is_larger(tmp_path):
    # Same price shape as the first test, but with first/second swapped -- the
    # buy/sell condition flips accordingly (no built-in "first must be smaller").
    closes = [10, 10, 10, 20, 20, 20, 5, 5, 5]
    start_day = date(2024, 1, 2)
    dates = [start_day + timedelta(days=i) for i in range(len(closes))]

    store = CsvStore(tmp_path)
    repo = DataRepository(FakeProvider(_bars(dates, [float(c) for c in closes])), store)
    repo.ingest(["AAPL"], dates[0], dates[-1])

    strategy = MACrossoverStrategy(
        repo, "AAPL", first_window=2, second_window=3, buy_qty=10, sell_qty=10, portfolio_id="main"
    )
    portfolios = {"main": Portfolio("main", cash=10_000.0)}

    harness = BacktestHarness(repo, [strategy], portfolios, dates)
    harness.run()

    # first(2) > second(3) on the up-leg -> no buy there; instead it buys on the
    # down-leg once first(2) drops below second(3).
    trades = portfolios["main"].trades
    assert [t.side for t in trades] == [Side.BUY]
    assert trades[0].date == dates[6]
