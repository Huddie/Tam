import time
from pathlib import Path

import pandas as pd
import pytest

from examples.backtest import BacktestSettings, _validate_tickers_declared, run
from tam.backtest.runner import run_backtest
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


def test_run_live_mode_starts_backtest_in_background_and_calls_serve(tmp_path, monkeypatch):
    # Regression test: run(mode="live") must forward the same total_days to both
    # _run_batch (inside the background thread) and the Progress bar it builds --
    # a signature mismatch between _run_live and _run_batch previously crashed
    # this path with a TypeError before the backtest ever started.
    report_path = tmp_path / "out.html"
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
  report_path: {report_path}
  strategies:
    - strategy: moving_average
      portfolio_id: m
      params:
        ticker: TQQQ
        window: 3
        qty: 1
"""
    )

    serve_calls = []

    def fake_serve(checkpoint_path, title, ticker_colors=None, prices=None, port=8050, verbose=False):
        serve_calls.append((checkpoint_path, title, port, verbose))
        deadline = time.time() + 5
        while not report_path.exists() and time.time() < deadline:
            time.sleep(0.02)

    monkeypatch.setattr("tam.backtest.live.serve", fake_serve)

    run(config_path, mode="live")

    assert len(serve_calls) == 1
    checkpoint_path, _title, port, verbose = serve_calls[0]
    assert checkpoint_path  # auto-namespaced default was filled in
    assert port == 8050
    assert verbose is False
    assert report_path.exists()


def test_no_save_skips_checkpointing_in_batch_mode(tmp_path):
    report_path = tmp_path / "out.html"
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
  report_path: {report_path}
  strategies:
    - strategy: moving_average
      portfolio_id: m
      params:
        ticker: TQQQ
        window: 3
        qty: 1
"""
    )

    run(config_path, no_save=True)

    assert report_path.exists()
    assert not list(tmp_path.glob("output/**/checkpoint.pkl"))  # nothing resumable was ever written


def test_no_save_uses_an_ephemeral_checkpoint_for_live_mode(tmp_path, monkeypatch):
    # --mode live still needs *a* checkpoint file to drive the dashboard poll --
    # --no-save must give it a throwaway one outside the config's own
    # (persistent, resumable-by-design) artifacts dir, not skip it outright.
    report_path = tmp_path / "out.html"
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
  report_path: {report_path}
  strategies:
    - strategy: moving_average
      portfolio_id: m
      params:
        ticker: TQQQ
        window: 3
        qty: 1
"""
    )

    serve_calls = []

    def fake_serve(checkpoint_path, title, ticker_colors=None, prices=None, port=8050, verbose=False):
        serve_calls.append(checkpoint_path)
        deadline = time.time() + 5
        while not report_path.exists() and time.time() < deadline:
            time.sleep(0.02)

    monkeypatch.setattr("tam.backtest.live.serve", fake_serve)

    run(config_path, mode="live", no_save=True)

    assert len(serve_calls) == 1
    checkpoint_path = serve_calls[0]
    assert checkpoint_path
    assert not str(Path(checkpoint_path)).startswith(str(tmp_path))
    assert report_path.exists()


def _small_config(tmp_path: Path) -> Path:
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
    - strategy: moving_average
      portfolio_id: m
      params:
        ticker: TQQQ
        window: 3
        qty: 1
"""
    )
    return config_path


def test_run_backtest_returns_the_report_and_renders_inline(tmp_path, monkeypatch):
    # The notebook entry point (unlike the CLI's run()) must hand back the
    # Report itself, and must render via fig.show() -- Plotly's own
    # notebook-detection -- rather than writing an HTML file to disk for the
    # user to separately open.
    show_calls = []
    monkeypatch.setattr("plotly.graph_objs.Figure.show", lambda self, *a, **k: show_calls.append(self))

    report = run_backtest(_small_config(tmp_path))

    assert report is not None
    assert not report.summary_all().empty
    assert len(show_calls) == 1


def test_run_backtest_live_redraws_via_ipython_display_not_dash(tmp_path, monkeypatch):
    # live=True does NOT use Dash (unlike --mode live on the CLI, which opens
    # a real server for a real browser tab) -- Dash's own inline-in-notebook
    # support doesn't work in Colab (confirmed both from Dash's own source
    # and empirically). Instead it redraws the same chart in place via
    # IPython's display()/update_display(display_id=...), the same
    # rich-display mechanism the non-live path's fig.show() already uses.
    config_path = _small_config(tmp_path)

    display_calls = []
    update_calls = []
    monkeypatch.setattr("IPython.display.display", lambda fig, display_id: display_calls.append((fig, display_id)))
    monkeypatch.setattr(
        "IPython.display.update_display", lambda fig, display_id: update_calls.append((fig, display_id))
    )

    result = run_backtest(config_path, live=True)

    assert result is None  # live mode returns None -- no single Report at the moment it returns
    assert len(display_calls) == 1  # first frame -- establishes the notebook output slot
    display_id = display_calls[0][1]
    assert display_id  # a real id was generated
    # Every later frame (including the final one, redrawn once the
    # background thread finishes) updates that SAME slot in place, rather
    # than each one calling display() again and stacking a new plot
    # underneath every refresh.
    assert all(call_display_id == display_id for _fig, call_display_id in update_calls)
