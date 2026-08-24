from datetime import date, timedelta

import pandas as pd
import pytest

from tam.backtest.report import Report
from tam.backtest.visualization import render_curves
from tam.basket.simulate import basket_wealth_curve, simulate_basket


def _index(periods, start=date(2024, 1, 1)):
    return pd.to_datetime([start + timedelta(days=i) for i in range(periods)])


def test_simulate_basket_is_the_weighted_sum_of_component_returns():
    idx = _index(3)
    returns = pd.DataFrame({"A": [0.01, 0.02, -0.01], "B": [0.03, -0.01, 0.02]}, index=idx)

    basket = simulate_basket(returns, {"A": 0.5, "B": 0.5})

    assert list(basket) == pytest.approx([0.02, 0.005, 0.005])


def test_simulate_basket_treats_a_missing_day_as_zero_not_nan():
    idx = _index(3)
    returns = pd.DataFrame({"A": [0.01, None, -0.01], "B": [0.03, -0.01, 0.02]}, index=idx)

    basket = simulate_basket(returns, {"A": 0.5, "B": 0.5})

    assert not basket.isna().any()
    assert basket.iloc[1] == pytest.approx(0.5 * 0 + 0.5 * -0.01)


def test_simulate_basket_ignores_columns_not_in_weights():
    idx = _index(2)
    returns = pd.DataFrame({"A": [0.01, 0.02], "B": [0.03, -0.01], "C": [1.0, 1.0]}, index=idx)

    basket = simulate_basket(returns, {"A": 1.0})

    assert list(basket) == pytest.approx([0.01, 0.02])


def test_basket_wealth_curve_compounds_from_starting_cash():
    idx = _index(2)
    returns = pd.DataFrame({"A": [0.10, -0.10]}, index=idx)

    wealth = basket_wealth_curve(returns, {"A": 1.0}, starting_cash=1000.0)

    assert wealth.iloc[0] == pytest.approx(1100.0)
    assert wealth.iloc[1] == pytest.approx(1100.0 * 0.9)


def test_basket_wealth_curve_composes_with_report_and_render_curves():
    # The actual point of this module: compare two candidate configs by
    # feeding both curves straight into the exact same rendering/report
    # machinery a real backtest's Report already uses.
    idx = _index(5)
    returns = pd.DataFrame({"A": [0.01, 0.02, -0.01, 0.03, 0.0], "B": [0.0, 0.01, 0.02, -0.01, 0.01]}, index=idx)

    curve_a = basket_wealth_curve(returns, {"A": 1.0})
    curve_b = basket_wealth_curve(returns, {"B": 1.0})

    report = Report.from_curves({"config_a": curve_a, "config_b": curve_b})
    assert set(report.portfolio_ids()) == {"config_a", "config_b"}

    fig = render_curves({"config_a": curve_a, "config_b": curve_b})
    trace_names = {t.name for t in fig.data}
    assert {"config_a", "config_b"}.issubset(trace_names)
