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

    def fake_serve(checkpoint_path, title, ticker_colors=None, prices=None, port=8050, verbose=False, poll_seconds=3.0, options=None):
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

    def fake_serve(checkpoint_path, title, ticker_colors=None, prices=None, port=8050, verbose=False, poll_seconds=3.0, options=None):
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

    report = run_backtest(_small_config(tmp_path), live=False)

    assert report is not None
    assert not report.summary_all().empty
    assert len(show_calls) == 1


def test_run_backtest_live_redraws_via_clear_output_not_dash(tmp_path, monkeypatch):
    # live=True does NOT use Dash (unlike --mode live on the CLI, which opens
    # a real server for a real browser tab) -- Dash's own inline-in-notebook
    # support doesn't work in Colab (confirmed both from Dash's own source
    # and empirically). It also doesn't use IPython's
    # display(display_id=...)/update_display() -- Colab's frontend doesn't
    # reliably replace rich HTML/JS content (e.g. a Plotly figure) in place
    # via that mechanism (confirmed empirically: it kept stacking a new
    # chart underneath the old one instead of replacing it). Instead, every
    # redraw clears the cell's entire output first (clear_output(wait=True))
    # and displays the new figure fresh -- the standard "live plot in Colab"
    # pattern.
    config_path = _small_config(tmp_path)

    clear_calls = []
    display_calls = []
    monkeypatch.setattr("IPython.display.clear_output", lambda wait=False: clear_calls.append(wait))
    monkeypatch.setattr("IPython.display.display", lambda fig: display_calls.append(fig))

    result = run_backtest(config_path, live=True)

    assert result is None  # live mode returns None -- no single Report at the moment it returns
    assert len(display_calls) >= 1  # at least the final frame got drawn
    assert len(clear_calls) == len(display_calls)  # every display() is preceded by a clear_output()
    assert all(wait is True for wait in clear_calls)  # wait=True -- no visible blank flash between frames


def test_run_backtest_live_native_dash_uses_serve_with_jupyter_mode(tmp_path, monkeypatch):
    # render_mode="native_dash" opts into the real-Dash-server presenter --
    # kept available for classic Jupyter/JupyterLab (or in case Colab's own
    # support improves later) even though it's no longer the default.
    config_path = _small_config(tmp_path)

    show_calls = []
    monkeypatch.setattr("plotly.graph_objs.Figure.show", lambda self, *a, **k: show_calls.append(self))

    serve_calls = []

    def fake_serve(checkpoint_path, title, ticker_colors=None, prices=None, port=8050, verbose=False, jupyter_mode=None, poll_seconds=3.0, options=None):
        serve_calls.append({"jupyter_mode": jupyter_mode, "poll_seconds": poll_seconds})
        # Block until the background thread's tiny backtest actually
        # finishes and renders -- otherwise that daemon thread can still be
        # running (and calling the un-mocked real fig.show(), which tries to
        # open a real browser) after this test has already returned and
        # monkeypatch has undone its patches.
        deadline = time.time() + 5
        while not show_calls and time.time() < deadline:
            time.sleep(0.02)

    monkeypatch.setattr("tam.backtest.live.serve", fake_serve)

    result = run_backtest(config_path, live=True, render_mode="native_dash")

    assert result is None
    assert len(serve_calls) == 1
    assert serve_calls[0]["jupyter_mode"] == "inline"  # DashNotebookPresenter's own default
    assert len(show_calls) == 1  # the background run finished and rendered before we returned


def test_run_backtest_presenter_kwargs_forwards_to_the_chosen_presenter(tmp_path, monkeypatch):
    # presenter_kwargs is passed straight through to whichever Presenter
    # render_mode selects -- e.g. a custom jupyter_mode/poll_seconds for
    # native_dash, without run_backtest needing to know those exist.
    config_path = _small_config(tmp_path)

    show_calls = []
    monkeypatch.setattr("plotly.graph_objs.Figure.show", lambda self, *a, **k: show_calls.append(self))

    serve_calls = []

    def fake_serve(checkpoint_path, title, ticker_colors=None, prices=None, port=8050, verbose=False, jupyter_mode=None, poll_seconds=3.0, options=None):
        serve_calls.append({"jupyter_mode": jupyter_mode, "poll_seconds": poll_seconds})
        deadline = time.time() + 5
        while not show_calls and time.time() < deadline:
            time.sleep(0.02)

    monkeypatch.setattr("tam.backtest.live.serve", fake_serve)

    run_backtest(
        config_path,
        live=True,
        render_mode="native_dash",
        presenter_kwargs={"jupyter_mode": "external", "poll_seconds": 5.0},
    )

    assert serve_calls == [{"jupyter_mode": "external", "poll_seconds": 5.0}]


def test_run_backtest_rejects_an_unknown_render_mode(tmp_path):
    config_path = _small_config(tmp_path)

    with pytest.raises(ValueError, match="render_mode"):
        run_backtest(config_path, live=True, render_mode="not_a_real_mode")
