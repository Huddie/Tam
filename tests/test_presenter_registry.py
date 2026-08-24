"""Presenter registry + report: config wiring (tam/backtest/{presenter,runner}.py).

Follows this test suite's existing convention (see test_visualization.py,
test_report.py) of building minimal fakes directly rather than driving a full
CLI/backtest integration -- see test_backtest_cli.py for that heavier style
where it's already established (e.g. render_mode/presenter_kwargs coverage).
"""
import pandas as pd
import pytest

from tam.backtest.presenter import CliPresenter, DashNotebookPresenter, NotebookPresenter, Presenter
from tam.backtest.report import Report
from tam.backtest.runner import ReportSettings, run, run_backtest
from tam.config import Config
from tam.data.providers import DataProvider
from tam.data.schema import OHLCV_COLUMNS
from tam.registry import Registry


def test_builtin_presenters_are_registered_under_their_render_mode_names():
    assert Registry.create(Presenter, "cli", report_path="out.html").__class__ is CliPresenter
    assert Registry.create(Presenter, "clear_output").__class__ is NotebookPresenter
    assert Registry.create(Presenter, "native_dash").__class__ is DashNotebookPresenter


def test_registry_create_rejects_an_unregistered_presenter_name():
    with pytest.raises(KeyError, match="not_a_real_presenter"):
        Registry.create(Presenter, "not_a_real_presenter")


def test_report_config_section_round_trips_into_report_settings(tmp_path):
    config_path = tmp_path / "cfg.yaml"
    config_path.write_text(
        """
report:
  presenter: cli
  presenter_kwargs:
    poll_seconds: 2.5
  show_trades_default: false
  height: 900
"""
    )
    report_settings = Config(config_path).report(ReportSettings)

    assert report_settings.presenter == "cli"
    assert dict(report_settings.presenter_kwargs) == {"poll_seconds": 2.5}
    assert report_settings.show_trades_default is False
    assert report_settings.height == 900


class _FakePresenter(Presenter):
    """Minimal conforming implementation -- proof that "just write a class
    that conforms to the interface and pass it in" needs no @Registry.register
    and no other file to change."""

    def __init__(self):
        self.batch_calls = 0
        self.shown = []
        self.live_calls = 0

    def run_batch(self, harness, total_days, checkpoint_path, checkpoint_every):
        self.batch_calls += 1
        return harness.run(checkpoint_path=checkpoint_path, checkpoint_every=checkpoint_every)

    def show_report(self, report, title, ticker_colors, prices):
        self.shown.append(report)

    def run_live(self, *args, **kwargs):
        self.live_calls += 1


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


@Registry.register(DataProvider, "fake_presenter_registry_provider")
class _FakeProvider(DataProvider):
    def fetch_eod(self, symbol, start, end):
        df = _FAKE_PRICES
        return df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]


def _small_config(tmp_path, report_block: str = ""):
    config_path = tmp_path / "cfg.yaml"
    config_path.write_text(
        f"""
data:
  provider: fake_presenter_registry_provider
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
{report_block}
"""
    )
    return config_path


def test_run_backtest_with_a_custom_presenter_instance_bypasses_the_registry(tmp_path):
    config_path = _small_config(tmp_path)
    fake = _FakePresenter()

    report = run_backtest(config_path, presenter=fake)

    assert fake.batch_calls == 1
    assert fake.shown == [report]
    assert isinstance(report, Report)


def test_run_with_a_custom_presenter_instance_bypasses_the_registry(tmp_path):
    config_path = _small_config(tmp_path)
    fake = _FakePresenter()

    run(config_path, presenter=fake)

    assert fake.batch_calls == 1
    assert len(fake.shown) == 1


def test_run_backtest_show_trades_default_overrides_config(tmp_path, monkeypatch):
    # report.show_trades_default: true in the config, overridden to False by
    # the Python kwarg -- the kwarg should win, and the resolved RenderOptions
    # should reach visualization.render() (NotebookPresenter.show_report's
    # `from .visualization import render` binds whatever tam.backtest.
    # visualization.render currently is at call time, so patching the module
    # attribute here is visible to it).
    config_path = _small_config(tmp_path, report_block="report:\n  show_trades_default: true\n")

    captured = {}

    class _FakeFigure:
        def show(self):
            pass

    def fake_render(*args, **kwargs):
        captured["options"] = kwargs.get("options")
        return _FakeFigure()

    monkeypatch.setattr("tam.backtest.visualization.render", fake_render)

    run_backtest(config_path, show_trades_default=False)

    assert captured["options"].show_trades_default is False


def test_run_backtest_rejects_an_unknown_render_mode(tmp_path):
    config_path = _small_config(tmp_path)

    with pytest.raises(ValueError, match="render_mode"):
        run_backtest(config_path, live=True, render_mode="not_a_real_mode")
