from datetime import date, timedelta

import pandas as pd

from tam.backtest.harness import BacktestHarness
from tam.backtest.live import report_from_checkpoint
from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.schema import OHLCV_COLUMNS
from tam.data.storage import CsvStore
from tam.portfolio.orders import Side
from tam.portfolio.portfolio import Portfolio
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


_DATES = [date(2024, 1, 2) + timedelta(days=i) for i in range(5)]
_CLOSES = [100.0, 102.0, 98.0, 105.0, 110.0]


def test_report_from_checkpoint_is_none_when_no_file_exists(tmp_path):
    assert report_from_checkpoint(str(tmp_path / "does_not_exist.pkl")) is None


def test_report_from_checkpoint_reconstructs_snapshots_and_trades_mid_run(tmp_path):
    store = CsvStore(tmp_path / "store")
    repo = DataRepository(FakeProvider(_bars(_DATES, _CLOSES)), store)
    repo.ingest(["AAPL"], _DATES[0], _DATES[-1])

    checkpoint_path = tmp_path / "checkpoint.pkl"
    strategy = BuyAndHoldStrategy(ticker="AAPL", qty=5, portfolio_id="main")
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, _DATES)

    # Manually write one checkpoint partway through, the same way run() would,
    # without running the whole backtest to completion (which deletes it).
    harness.run(checkpoint_path=str(checkpoint_path), checkpoint_every=1)
    # A clean finish deletes the checkpoint -- write a fresh one directly so this
    # test exercises reading a *live*, not-yet-finished checkpoint.
    harness._write_checkpoint(str(checkpoint_path), day_index=3, snapshots=[
        {"date": _DATES[0], "portfolio": "main", "cash": 9_500.0, "value": 10_000.0},
        {"date": _DATES[1], "portfolio": "main", "cash": 9_500.0, "value": 10_010.0},
    ])

    report = report_from_checkpoint(str(checkpoint_path))

    assert report is not None
    assert len(report.snapshots) == 2
    assert report.trades == [
        {"date": _DATES[0], "portfolio": "main", "ticker": "AAPL", "side": Side.BUY, "qty": 5, "price": 100.0}
    ]


def test_report_from_checkpoint_is_none_again_after_a_clean_finish(tmp_path):
    store = CsvStore(tmp_path / "store")
    repo = DataRepository(FakeProvider(_bars(_DATES, _CLOSES)), store)
    repo.ingest(["AAPL"], _DATES[0], _DATES[-1])

    checkpoint_path = tmp_path / "checkpoint.pkl"
    strategy = BuyAndHoldStrategy(ticker="AAPL", qty=5, portfolio_id="main")
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, _DATES)

    harness.run(checkpoint_path=str(checkpoint_path), checkpoint_every=1)

    assert report_from_checkpoint(str(checkpoint_path)) is None
