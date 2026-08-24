from datetime import date, timedelta

import pandas as pd
import pytest

from tam.backtest.harness import BacktestHarness
from tam.backtest.live import live_render, report_from_checkpoint, serve
from tam.backtest.report import Report
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


def test_live_render_redraws_via_clear_output_using_a_custom_next_frame(monkeypatch):
    # No BacktestHarness/checkpoint file at all -- next_frame is driven entirely
    # by the caller's own loop, e.g. a vectorized numpy backtest wrapping its
    # own running Series in Report.from_curves() each tick. This is the
    # composable live-update entry point NotebookPresenter.run_live itself is
    # now just a thin wrapper around (see presenter.py).
    idx = pd.to_datetime([date(2024, 1, 1) + timedelta(days=i) for i in range(3)])
    tick = {"n": 0}

    def next_frame():
        tick["n"] = min(tick["n"] + 1, 3)
        n = tick["n"]
        return Report.from_curves({"toy": pd.Series(range(100, 100 + n), index=idx[:n])})

    clear_calls = []
    display_calls = []
    monkeypatch.setattr("IPython.display.clear_output", lambda wait=False: clear_calls.append(wait))
    monkeypatch.setattr("IPython.display.display", lambda fig: display_calls.append(fig))

    live_render(next_frame, poll_seconds=0, should_continue=lambda: tick["n"] < 3)

    assert tick["n"] == 3
    # 3 loop ticks + 1 final redraw after should_continue() goes False.
    assert len(display_calls) == len(clear_calls) == 4
    assert all(wait is True for wait in clear_calls)


def test_live_render_never_draws_if_next_frame_always_returns_none(monkeypatch):
    display_calls = []
    monkeypatch.setattr("IPython.display.clear_output", lambda wait=False: None)
    monkeypatch.setattr("IPython.display.display", lambda fig: display_calls.append(fig))

    calls = {"n": 0}

    def next_frame():
        calls["n"] += 1
        return None

    live_render(next_frame, poll_seconds=0, should_continue=lambda: calls["n"] < 2)

    assert display_calls == []  # nothing to draw yet, so it never called display()


def test_serve_requires_exactly_one_of_checkpoint_path_or_next_frame():
    with pytest.raises(ValueError, match="checkpoint_path or next_frame"):
        serve()
    with pytest.raises(ValueError, match="checkpoint_path or next_frame"):
        serve(checkpoint_path="x.pkl", next_frame=lambda: None)
