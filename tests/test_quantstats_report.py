"""QuantStats adapter (tam/backtest/quantstats_report.py): returns_for()/
resolve_benchmark() are pure pandas and run unconditionally -- the tests that
actually call quantstats' own functions with their output need the real
`quantstats` extra, guarded with pytest.importorskip so this file skips
cleanly wherever that extra isn't installed instead of failing the whole
suite.
"""

from datetime import date, timedelta

import pandas as pd
import pytest

from tam.backtest.quantstats_report import resolve_benchmark, returns_for
from tam.backtest.report import Report

quantstats = pytest.importorskip("quantstats")


def _series(values, start=date(2024, 1, 1)):
    idx = pd.to_datetime([start + timedelta(days=i) for i in range(len(values))])
    return pd.Series(values, index=idx)


def _two_portfolio_report():
    main = _series([100.0, 101.0, 99.0, 103.0, 105.0, 104.0, 108.0, 110.0, 107.0, 112.0])
    alt = _series([100.0, 100.5, 100.2, 101.0, 100.8, 101.5, 101.2, 102.0, 101.8, 102.5])
    return Report.from_curves({"main": main, "alt": alt})


def test_returns_for_matches_manual_pct_change_and_has_a_datetime_index():
    report = _two_portfolio_report()

    returns = returns_for(report, "main")

    curve = report.equity_curve("main")
    expected = curve.pct_change().dropna()
    assert list(returns) == pytest.approx(list(expected))
    assert len(returns) == len(curve) - 1
    assert isinstance(returns.index, pd.DatetimeIndex)


def test_returns_for_coerces_a_plain_date_index_to_datetime():
    # A harness-produced Report snapshots dates as plain datetime.date, not
    # pd.Timestamp -- returns_for() must still hand quantstats a real
    # DatetimeIndex (its own resampling needs one).
    snapshots = [
        {"date": date(2024, 1, 1) + timedelta(days=i), "portfolio": "main", "cash": 0.0, "value": v}
        for i, v in enumerate([100.0, 102.0, 101.0, 105.0])
    ]
    report = Report(snapshots)

    returns = returns_for(report, "main")

    assert isinstance(returns.index, pd.DatetimeIndex)


def test_resolve_benchmark_uses_another_portfolio_in_the_same_report_with_no_network():
    report = _two_portfolio_report()

    resolved = resolve_benchmark(report, "alt")

    assert isinstance(resolved, pd.Series)
    assert list(resolved) == pytest.approx(list(returns_for(report, "alt")))


def test_resolve_benchmark_passes_through_an_unmatched_string_ticker():
    report = _two_portfolio_report()

    assert resolve_benchmark(report, "SPY") == "SPY"


def test_resolve_benchmark_passes_through_none():
    report = _two_portfolio_report()

    assert resolve_benchmark(report, None) is None


def test_native_quantstats_metrics_accepts_our_returns_and_resolved_benchmark():
    report = _two_portfolio_report()

    qs_metrics = quantstats.reports.metrics(
        returns_for(report, "main"), benchmark=resolve_benchmark(report, "alt"), display=False
    )
    our_summary = report.summary("main")

    assert qs_metrics.shape[1] == 2  # Strategy + Benchmark columns
    assert len(qs_metrics) > len(our_summary)


def test_native_quantstats_html_report_accepts_our_returns_and_resolved_benchmark(tmp_path):
    from tam.backtest.visualization import write_html as our_write_html

    report = _two_portfolio_report()
    dashboard_path = tmp_path / "dashboard.html"
    tearsheet_path = tmp_path / "tearsheet.html"

    our_write_html(report, str(dashboard_path))
    quantstats.reports.html(
        returns_for(report, "main"),
        benchmark=resolve_benchmark(report, "alt"),
        output=str(tearsheet_path),
        title="Main vs Alt",
    )

    assert dashboard_path.exists() and dashboard_path.stat().st_size > 0
    assert tearsheet_path.exists() and tearsheet_path.stat().st_size > 0


def test_native_quantstats_snapshot_plot_accepts_our_returns_with_no_benchmark():
    import matplotlib
    import matplotlib.pyplot as plt

    matplotlib.use("Agg")
    report = _two_portfolio_report()

    try:
        fig = quantstats.plots.snapshot(returns_for(report, "main"), show=False)
        assert fig is not None
    finally:
        plt.close("all")


def test_native_quantstats_rolling_sharpe_plot_accepts_our_resolved_benchmark():
    import matplotlib
    import matplotlib.pyplot as plt

    matplotlib.use("Agg")
    report = _two_portfolio_report()

    try:
        fig = quantstats.plots.rolling_sharpe(
            returns_for(report, "main"), benchmark=resolve_benchmark(report, "alt"), show=False, period=3
        )
        assert fig is not None
    finally:
        plt.close("all")
