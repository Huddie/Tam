"""tam/notebook/magic.py -- the %backtest line magic.

No real IPython kernel needed: `load_ipython_extension` only touches the
`ipython` object it's handed, so a tiny fake standing in for
`register_magic_function` is enough to test the wiring in isolation.
"""
from tam.notebook.magic import load_ipython_extension, run_backtest_magic


class _FakeIPython:
    def __init__(self):
        self.registered = []

    def register_magic_function(self, func, magic_kind, magic_name):
        self.registered.append((func, magic_kind, magic_name))


def test_load_ipython_extension_registers_backtest_as_a_line_magic():
    ipython = _FakeIPython()

    load_ipython_extension(ipython)

    assert len(ipython.registered) == 1
    func, magic_kind, magic_name = ipython.registered[0]
    assert func is run_backtest_magic
    assert magic_kind == "line"
    assert magic_name == "backtest"


def test_run_backtest_magic_parses_the_line_and_forwards_to_run_backtest(monkeypatch):
    captured = {}

    def fake_run_backtest(config_path, **kwargs):
        captured["config_path"] = config_path
        captured["kwargs"] = kwargs
        return "the-report"

    monkeypatch.setattr("tam.notebook.magic.run_backtest", fake_run_backtest)

    result = run_backtest_magic("config.yaml --live --render-mode native_dash --poll-seconds 5 --show-trades false --port 9000")

    assert result == "the-report"
    assert captured["config_path"] == "config.yaml"
    assert captured["kwargs"] == {
        "live": True,
        "port": 9000,
        "render_mode": "native_dash",
        "presenter_kwargs": {"poll_seconds": 5.0},
        "show_trades_default": False,
    }


def test_run_backtest_magic_defaults_are_all_none_or_batch_when_only_config_given(monkeypatch):
    captured = {}

    def fake_run_backtest(config_path, **kwargs):
        captured["config_path"] = config_path
        captured["kwargs"] = kwargs

    monkeypatch.setattr("tam.notebook.magic.run_backtest", fake_run_backtest)

    run_backtest_magic("config.yaml")

    assert captured["config_path"] == "config.yaml"
    assert captured["kwargs"] == {
        "live": False,
        "port": 8050,
        "render_mode": None,
        "presenter_kwargs": None,
        "show_trades_default": None,
    }
