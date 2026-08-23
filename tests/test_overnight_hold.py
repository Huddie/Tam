from datetime import date, timedelta

import pandas as pd

from tam.backtest.harness import BacktestHarness
from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.schema import OHLCV_COLUMNS
from tam.data.storage import CsvStore
from tam.portfolio.orders import Side
from tam.portfolio.portfolio import Portfolio
from tam.strategy.intraday_hold import IntradayHoldStrategy
from tam.strategy.overnight_hold import OvernightHoldStrategy


class FakeProvider(DataProvider):
    def __init__(self, frame: pd.DataFrame):
        self._frame = frame

    def fetch_eod(self, symbol, start, end):
        df = self._frame
        return df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]


def _bars(dates, opens, closes):
    index = pd.to_datetime(dates)
    return pd.DataFrame(
        {
            "open": opens,
            "high": [max(o, c) for o, c in zip(opens, closes)],
            "low": [min(o, c) for o, c in zip(opens, closes)],
            "close": closes,
            "adj_close": closes,
            "volume": [1_000] * len(opens),
        },
        index=index,
    ).rename_axis("date")[OHLCV_COLUMNS]


def _dates(n):
    start_day = date(2024, 1, 2)
    return [start_day + timedelta(days=i) for i in range(n)]


def test_overnight_hold_buys_at_close_and_sells_at_next_open(tmp_path):
    dates = _dates(3)
    opens = [100.0, 105.0, 108.0]
    closes = [102.0, 107.0, 110.0]

    store = CsvStore(tmp_path)
    repo = DataRepository(FakeProvider(_bars(dates, opens, closes)), store)
    repo.ingest(["MU"], dates[0], dates[-1])

    strategy = OvernightHoldStrategy("MU", qty={"pct": 100}, portfolio_id="main")
    portfolio = Portfolio("main", cash=10_000.0)

    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    trades = portfolio.trades
    # Day 1: buy at close (102). Day 2: sell at open (105), buy at close (107).
    # Day 3: sell at open (108), buy at close (110) -- left holding at the end.
    assert [(t.date, t.side, t.price) for t in trades] == [
        (dates[0], Side.BUY, 102.0),
        (dates[1], Side.SELL, 105.0),
        (dates[1], Side.BUY, 107.0),
        (dates[2], Side.SELL, 108.0),
        (dates[2], Side.BUY, 110.0),
    ]
    # Overnight-only P&L across both nights: buy@102 sell@105 (qty floor(10000/102)=98),
    # buy@107 sell@108 (qty floor(cash after day1/day2 trades / 107)).
    assert portfolio.position("MU").qty > 0


def test_intraday_hold_buys_at_open_and_sells_at_same_day_close(tmp_path):
    dates = _dates(2)
    opens = [100.0, 105.0]
    closes = [102.0, 103.0]

    store = CsvStore(tmp_path)
    repo = DataRepository(FakeProvider(_bars(dates, opens, closes)), store)
    repo.ingest(["MU"], dates[0], dates[-1])

    strategy = IntradayHoldStrategy("MU", qty={"pct": 100}, portfolio_id="main")
    portfolio = Portfolio("main", cash=10_000.0)

    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    trades = portfolio.trades
    assert [(t.date, t.side, t.price) for t in trades] == [
        (dates[0], Side.BUY, 100.0),
        (dates[0], Side.SELL, 102.0),
        (dates[1], Side.BUY, 105.0),
        (dates[1], Side.SELL, 103.0),
    ]
    # Flat overnight every night -- no position carried between days.
    assert portfolio.position("MU").qty == 0
