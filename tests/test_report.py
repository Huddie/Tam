from datetime import date, timedelta

from tam.backtest.report import Report


def _snap(d, portfolio, value, cash=0.0):
    return {"date": d, "portfolio": portfolio, "cash": cash, "value": value}


def _series(portfolio, values, start=date(2024, 1, 1)):
    """Build a list of daily snapshot dicts for one portfolio from a list of values."""
    return [
        _snap(start + timedelta(days=i), portfolio, v)
        for i, v in enumerate(values)
    ]


def test_summary_total_return_and_max_drawdown():
    # Peak at 120 (day 1), trough at 90 (day 2) -> drawdown of -25%.
    values = [100.0, 120.0, 90.0, 110.0, 150.0]
    report = Report(_series("main", values))

    summary = report.summary("main")

    assert summary["portfolio"] == "main"
    assert summary["start_value"] == 100.0
    assert summary["end_value"] == 150.0
    assert summary["total_return"] == 150.0 / 100.0 - 1
    assert summary["max_drawdown"] == 90.0 / 120.0 - 1


def test_summary_calmar_consistent_with_cagr_and_max_drawdown():
    values = [100.0, 120.0, 90.0, 110.0, 150.0]
    report = Report(_series("main", values))

    summary = report.summary("main")

    assert summary["max_drawdown"] != 0.0
    expected_calmar = summary["cagr"] / abs(summary["max_drawdown"])
    assert summary["calmar"] == expected_calmar


def test_summary_calmar_zero_when_no_drawdown():
    # Strictly increasing series never dips below its running peak.
    values = [100.0, 105.0, 110.0, 120.0]
    report = Report(_series("main", values))

    summary = report.summary("main")

    assert summary["max_drawdown"] == 0.0
    assert summary["calmar"] == 0.0


def test_summary_sharpe_positive_for_increasing_series():
    values = [100.0, 110.0, 118.0, 130.0]
    report = Report(_series("main", values))

    summary = report.summary("main")

    assert summary["sharpe"] > 0


def test_summary_sharpe_negative_for_decreasing_series():
    values = [130.0, 118.0, 110.0, 100.0]
    report = Report(_series("main", values))

    summary = report.summary("main")

    assert summary["sharpe"] < 0


def test_summary_single_snapshot_is_all_zero():
    single_report = Report(_series("main", [100.0]))
    summary_single = single_report.summary("main")
    assert summary_single["portfolio"] == "main"
    for key, value in summary_single.items():
        if key != "portfolio":
            assert value == 0.0


def test_summary_on_totally_empty_report_is_all_zero():
    summary = Report([]).summary("main")
    assert summary["portfolio"] == "main"
    for key, value in summary.items():
        if key != "portfolio":
            assert value == 0.0


def test_summary_all_matches_individual_summaries_for_two_portfolios():
    main_values = [100.0, 120.0, 90.0, 110.0, 150.0]
    alt_values = [200.0, 190.0, 210.0, 220.0]
    snapshots = _series("main", main_values) + _series("alt", alt_values)
    report = Report(snapshots)

    table = report.summary_all()

    assert list(table.index) == ["alt", "main"]
    assert len(table) == 2

    for portfolio_id in ("main", "alt"):
        individual = report.summary(portfolio_id)
        for key, value in individual.items():
            if key == "portfolio":
                continue
            assert table.loc[portfolio_id, key] == value


def test_drawdown_curve_always_nonpositive_and_zero_at_peak():
    values = [100.0, 120.0, 90.0, 110.0, 150.0]
    report = Report(_series("main", values))

    drawdown = report.drawdown_curve("main")

    assert (drawdown <= 0).all()
    assert (drawdown == 0).any()
    # The first two points are running peaks, so drawdown should be exactly 0 there.
    assert drawdown.iloc[0] == 0.0
    assert drawdown.iloc[1] == 0.0
    # The trough at value 90 (after peak 120) should show a -25% drawdown.
    assert drawdown.iloc[2] == 90.0 / 120.0 - 1


def test_trades_for_filters_and_sorts_by_date():
    trades = [
        {"date": date(2024, 1, 3), "portfolio": "main", "ticker": "AAPL", "side": "SELL", "qty": 5, "price": 110.0},
        {"date": date(2024, 1, 2), "portfolio": "alt", "ticker": "SPY", "side": "BUY", "qty": 1, "price": 400.0},
        {"date": date(2024, 1, 2), "portfolio": "main", "ticker": "AAPL", "side": "BUY", "qty": 10, "price": 100.0},
    ]
    report = Report(snapshots=[], trades=trades)

    main_trades = report.trades_for("main")
    assert list(main_trades["date"]) == [date(2024, 1, 2), date(2024, 1, 3)]
    assert set(main_trades["portfolio"]) == {"main"}


def test_trades_for_empty_when_no_trades_recorded():
    report = Report(snapshots=[], trades=[])
    assert report.trades_for("main").empty


def test_portfolio_ids_empty_and_sorted_unique():
    assert Report([]).portfolio_ids() == []

    snapshots = (
        _series("zeta", [1.0, 2.0])
        + _series("alpha", [1.0, 2.0])
        + _series("alpha", [3.0, 4.0], start=date(2024, 2, 1))
    )
    report = Report(snapshots)

    assert report.portfolio_ids() == ["alpha", "zeta"]
