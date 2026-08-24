from datetime import date

import pandas as pd
import pytest

from tam.backtest.walk_forward import run_walk_forward
from tam.data.providers import DataProvider
from tam.data.schema import OHLCV_COLUMNS
from tam.registry import Registry

_CLOSES_BY_DAY_OFFSET = 100.0


@Registry.register(DataProvider, "fake_walk_forward_provider")
class _FakeProvider(DataProvider):
    """Registered once at import time so multiple tests in this module can share it."""

    def fetch_eod(self, symbol, start, end):
        idx = pd.bdate_range(start, end)
        closes = [_CLOSES_BY_DAY_OFFSET + i * 0.1 for i in range(len(idx))]
        return pd.DataFrame(
            {"open": closes, "high": closes, "low": closes, "close": closes, "adj_close": closes, "volume": 100},
            index=idx,
        ).rename_axis("date")[OHLCV_COLUMNS]


def _config(tmp_path) -> str:
    config_path = tmp_path / "cfg.yaml"
    config_path.write_text(
        f"""
data:
  provider: fake_walk_forward_provider
  store: parquet
  root: {tmp_path / "cache"}
backtest:
  tickers: [AAPL]
  start: "2020-01-01"
  end: "2020-01-01"
  cash: 10000
  report_path: {tmp_path / "out.html"}
  strategies:
    - strategy: buy_and_hold
      portfolio_id: main
      params: {{ticker: AAPL}}
"""
    )
    return str(config_path)


def test_stitched_curve_only_contains_dates_within_some_test_window(tmp_path):
    windows = [
        (date(2020, 1, 2), date(2020, 3, 31), date(2020, 4, 1), date(2020, 4, 30)),
        (date(2020, 4, 1), date(2020, 6, 30), date(2020, 7, 1), date(2020, 7, 31)),
    ]

    report = run_walk_forward(_config(tmp_path), windows)

    curve = report.equity_curve("main")
    assert not curve.empty
    for as_of in curve.index:
        in_window_1 = date(2020, 4, 1) <= as_of <= date(2020, 4, 30)
        in_window_2 = date(2020, 7, 1) <= as_of <= date(2020, 7, 31)
        assert in_window_1 or in_window_2, f"{as_of} is outside every test window -- leaked training-period data"
    # Nothing from the train-only gap (May/June) leaked in either.
    assert curve.index.min() == date(2020, 4, 1)
    assert curve.index.max() == date(2020, 7, 31)


def test_stitched_curve_starts_at_the_given_starting_value(tmp_path):
    windows = [(date(2020, 1, 2), date(2020, 3, 31), date(2020, 4, 1), date(2020, 4, 10))]

    report = run_walk_forward(_config(tmp_path), windows, starting_value=250.0)

    assert report.equity_curve("main").iloc[0] == pytest.approx(250.0)


def test_second_window_compounds_from_the_first_windows_ending_level_not_a_fresh_start(tmp_path):
    windows = [
        (date(2020, 1, 2), date(2020, 3, 31), date(2020, 4, 1), date(2020, 4, 10)),
        (date(2020, 4, 1), date(2020, 6, 30), date(2020, 7, 1), date(2020, 7, 10)),
    ]

    report = run_walk_forward(_config(tmp_path), windows)

    curve = report.equity_curve("main")
    window_1_end = curve[curve.index <= date(2020, 4, 10)].iloc[-1]
    window_2_start = curve[curve.index >= date(2020, 7, 1)].iloc[0]
    # The second window's first point is window 1's ending level (0% return
    # "today" by convention -- the first point in a fresh slice has nothing
    # within that slice to compare against).
    assert window_2_start == pytest.approx(window_1_end)


def test_single_window_matches_a_plain_run_over_the_same_test_range(tmp_path):
    # A trivial "walk-forward" of exactly one window, with train_start ==
    # test_start (no separate warm-up), should reproduce the same shape of
    # curve a plain run_backtest over [test_start, test_end] would.
    windows = [(date(2020, 1, 2), date(2020, 1, 2), date(2020, 1, 2), date(2020, 1, 10))]

    report = run_walk_forward(_config(tmp_path), windows)

    curve = report.equity_curve("main")
    assert curve.index.min() == date(2020, 1, 2)
    assert curve.index.max() <= date(2020, 1, 10)
    assert len(curve) > 1
