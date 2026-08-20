from datetime import date, timedelta

import pandas as pd

from tam.backtest.harness import BacktestHarness
from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.schema import OHLCV_COLUMNS
from tam.data.storage import CsvStore
from tam.portfolio.orders import Side
from tam.portfolio.portfolio import Portfolio
from tam.strategy.trend_rotation import TrendRotationStrategy


class MultiTickerProvider(DataProvider):
    """Serves a distinct close-price series per symbol; unknown symbols get a
    flat $10 series (enough for tradable instruments whose own price doesn't
    matter to the test, only that they can be bought/sold)."""

    def __init__(self, series: dict[str, list[float]]):
        self._series = series

    def fetch_eod(self, symbol, start, end):
        dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        closes = self._series.get(symbol, [10.0] * len(dates))
        return _bars(dates, closes[: len(dates)])


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


def _setup(tmp_path, signal_closes, dates):
    store = CsvStore(tmp_path)
    provider = MultiTickerProvider({"QQQ": signal_closes})
    repo = DataRepository(provider, store)
    repo.ingest(["QQQ", "TQQQ", "SQQQ"], dates[0], dates[-1])
    return repo


def test_trend_rotation_flips_from_long_to_short_when_regime_reverses(tmp_path):
    # trend_window=3, momentum_window=2 -> required=3 days before any signal.
    closes = [10, 10, 10, 20, 30, 40, 10, 5, 1]
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(len(closes))]
    repo = _setup(tmp_path, [float(c) for c in closes], dates)

    strategy = TrendRotationStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", trend_window=3, momentum_window=2, buy_qty=10, sell_qty=10, portfolio_id="main"
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    trades = portfolio.trades
    assert [(t.date, t.ticker, t.side) for t in trades] == [
        (dates[3], "TQQQ", Side.BUY),
        (dates[6], "TQQQ", Side.SELL),
        (dates[6], "SQQQ", Side.BUY),
    ]


def test_trend_rotation_holds_position_when_signals_disagree(tmp_path):
    # trend_window=5, momentum_window=2 -> required=5. idx4: both signals bullish
    # -> buy TQQQ. idx5: trend still (barely) bullish but momentum turns negative
    # -> a 1-1 tie should hold the existing long position, not flip or re-buy.
    closes = [10, 11, 12, 13, 14, 12.8]
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(len(closes))]
    repo = _setup(tmp_path, [float(c) for c in closes], dates)

    strategy = TrendRotationStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", trend_window=5, momentum_window=2, buy_qty=10, sell_qty=10, portfolio_id="main"
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    trades = portfolio.trades
    assert len(trades) == 1
    assert trades[0].date == dates[4]
    assert trades[0].side == Side.BUY
    assert trades[0].ticker == "TQQQ"


def test_trend_rotation_requires_full_lookback_before_trading(tmp_path):
    closes = [10.0, 11.0, 12.0]
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(len(closes))]
    repo = _setup(tmp_path, closes, dates)

    strategy = TrendRotationStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", trend_window=5, momentum_window=2, buy_qty=10, sell_qty=10, portfolio_id="main"
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert portfolio.trades == []
