from pathlib import Path

import pandas as pd
import pytest

from examples.backtest import BacktestSettings, _validate_tickers_declared, run
from tam.config import Config
from tam.data.providers import DataProvider
from tam.data.schema import OHLCV_COLUMNS
from tam.registry import Registry


def test_validate_tickers_declared_passes_when_every_strategy_ticker_is_listed(tmp_path):
    config_path = tmp_path / "cfg.yaml"
    config_path.write_text(
        """
backtest:
  tickers: [TQQQ, SPY]
  start: "2024-01-01"
  end: "2024-01-05"
  cash: 1000
  report_path: out.html
  strategies:
    - strategy: buy_and_hold
      portfolio_id: b
      params:
        ticker: SPY
    - strategy: moving_average
      portfolio_id: m
      params:
        ticker: TQQQ
        window: 5
        qty: 1
"""
    )
    backtest_settings = Config(config_path).backtest(BacktestSettings)

    _validate_tickers_declared(backtest_settings)  # should not raise


def test_validate_tickers_declared_raises_when_a_strategy_ticker_is_undeclared(tmp_path):
    config_path = tmp_path / "cfg.yaml"
    config_path.write_text(
        """
backtest:
  tickers: [TQQQ]
  start: "2024-01-01"
  end: "2024-01-05"
  cash: 1000
  report_path: out.html
  strategies:
    - strategy: buy_and_hold
      portfolio_id: b
      params:
        ticker: SPY
"""
    )
    backtest_settings = Config(config_path).backtest(BacktestSettings)

    with pytest.raises(ValueError, match="SPY"):
        _validate_tickers_declared(backtest_settings)


_FAKE_PRICES = pd.DataFrame(
    {
        "open": [1.0] * 5,
        "high": [1.0] * 5,
        "low": [1.0] * 5,
        "close": [1.0, 1.1, 1.2, 1.3, 1.4],
        "adj_close": [1.0] * 5,
        "volume": [100] * 5,
    },
    index=pd.date_range("2024-01-02", periods=5),
).rename_axis("date")[OHLCV_COLUMNS]


@Registry.register(DataProvider, "fake_backtest_cli_provider")
class _FakeProvider(DataProvider):
    """Registered once at import time so multiple tests in this module can share it."""

    def fetch_eod(self, symbol, start, end):
        df = _FAKE_PRICES
        return df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]


def test_run_ingests_every_declared_ticker(tmp_path):
    # Regression test: buy_and_hold trades SPY while the primary ticker is TQQQ.
    # Before tickers became an explicit declared list, run() only ingested one
    # ticker, so SPY had no data and the buy_and_hold factory's auto-sizing
    # crashed on an empty query.
    report_path = tmp_path / "out.html"
    config_path = tmp_path / "cfg.yaml"
    config_path.write_text(
        f"""
data:
  provider: fake_backtest_cli_provider
  store: parquet
  root: {tmp_path / "eod"}
backtest:
  tickers: [TQQQ, SPY]
  start: "2024-01-02"
  end: "2024-01-06"
  cash: 1000
  report_path: {report_path}
  strategies:
    - strategy: moving_average
      portfolio_id: m
      params:
        ticker: TQQQ
        window: 3
        qty: 1
    - strategy: buy_and_hold
      portfolio_id: b
      params:
        ticker: SPY
"""
    )

    run(config_path)

    assert report_path.exists()
    assert report_path.stat().st_size > 0


def test_run_raises_before_ingesting_when_a_ticker_is_undeclared(tmp_path):
    config_path = tmp_path / "cfg.yaml"
    config_path.write_text(
        f"""
data:
  provider: fake_backtest_cli_provider
  store: parquet
  root: {tmp_path / "eod"}
backtest:
  tickers: [TQQQ]
  start: "2024-01-02"
  end: "2024-01-06"
  cash: 1000
  report_path: {tmp_path / "out.html"}
  strategies:
    - strategy: buy_and_hold
      portfolio_id: b
      params:
        ticker: SPY
"""
    )

    with pytest.raises(ValueError, match="SPY"):
        run(config_path)
