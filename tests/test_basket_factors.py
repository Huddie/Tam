from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tam.basket.factors import (
    ExpectedShortfall,
    MaxDrawdown,
    MeanReturn,
    OvernightAlpha,
    OvernightBeta,
    Persistence,
    RollingSharpe,
    compute_factors,
    score,
)


def _returns(values, start=date(2024, 1, 1)):
    idx = pd.to_datetime([start + timedelta(days=i) for i in range(len(values))])
    return pd.Series(values, index=idx)


def test_rolling_sharpe_matches_hand_computed_value():
    r = _returns([0.01, -0.01, 0.02, 0.0, 0.01])
    returns = pd.DataFrame({"A": r})
    as_of = r.index[-1].date()

    result = RollingSharpe(window_days=5).compute(returns, as_of)

    expected = r.mean() / r.std() * (252**0.5)
    assert result["A"] == pytest.approx(expected)


def test_rolling_sharpe_is_zero_not_nan_or_inf_when_std_is_zero():
    r = _returns([0.01, 0.01, 0.01, 0.01])
    returns = pd.DataFrame({"A": r})

    result = RollingSharpe(window_days=4).compute(returns, r.index[-1].date())

    assert result["A"] == 0.0


def test_mean_return_only_uses_the_trailing_window():
    r = _returns([1.0, 1.0, 1.0, 100.0, 100.0])  # window=2 should ignore the leading 1.0s
    returns = pd.DataFrame({"A": r})

    result = MeanReturn(window_days=2).compute(returns, r.index[-1].date())

    assert result["A"] == pytest.approx(100.0)


def test_factors_never_see_data_after_as_of():
    # The core point-in-time-safety property: corrupting rows strictly after
    # as_of must not change a factor's value computed at as_of.
    r = _returns(list(np.random.default_rng(0).normal(0, 0.01, 60)))
    returns = pd.DataFrame({"A": r})
    as_of = r.index[30].date()

    before = RollingSharpe(30).compute(returns, as_of)
    corrupted = returns.copy()
    corrupted.loc[corrupted.index > pd.Timestamp(as_of)] = 999.0
    after = RollingSharpe(30).compute(corrupted, as_of)

    assert before.equals(after)


def test_persistence_rewards_consistency_over_a_similar_long_run_average():
    # Stock A: steady small positive returns every period. Stock B: wild
    # swings averaging out similarly. A should score higher on persistence.
    rng = np.random.default_rng(1)
    steady = _returns([0.002] * 500)
    volatile = _returns((rng.normal(0.002, 0.05, 500)).tolist())
    returns = pd.DataFrame({"steady": steady, "volatile": volatile})
    as_of = returns.index[-1].date()

    result = Persistence(period_days=60).compute(returns, as_of)

    assert result["steady"] == 1.0
    assert result["steady"] > result["volatile"]


def test_overnight_alpha_and_beta_recover_a_known_linear_relationship():
    rng = np.random.default_rng(2)
    n = 300
    bench = _returns(rng.normal(0.0002, 0.008, n).tolist())
    # A tracks bench with beta=1.5 and a small positive alpha, plus noise.
    a = pd.Series(0.0004 + 1.5 * bench.values + rng.normal(0, 0.001, n), index=bench.index)
    returns = pd.DataFrame({"A": a, "SPY": bench})
    as_of = returns.index[-1].date()

    alpha = OvernightAlpha(window_days=n, benchmark="SPY").compute(returns, as_of)
    beta = OvernightBeta(window_days=n, benchmark="SPY").compute(returns, as_of)

    assert alpha["A"] == pytest.approx(0.0004, abs=0.0005)
    assert beta["A"] == pytest.approx(1.5, abs=0.1)
    # The benchmark against itself is trivially alpha=0, beta=1.
    assert alpha["SPY"] == 0.0
    assert beta["SPY"] == 1.0


def test_expected_shortfall_is_the_mean_of_the_worst_tail():
    r = _returns([0.01, 0.02, -0.20, -0.15, 0.03, 0.01, -0.01, 0.02, 0.01, -0.02])
    returns = pd.DataFrame({"A": r})

    result = ExpectedShortfall(window_days=10, confidence=0.8).compute(returns, r.index[-1].date())

    # Worst 20% (2 of 10 days) are -0.20 and -0.15.
    assert result["A"] == pytest.approx((-0.20 + -0.15) / 2)


def test_max_drawdown_matches_hand_computed_value():
    r = _returns([0.10, -0.20, 0.05])  # wealth: 1.10, 0.88, 0.924 -> trough 0.88 vs peak 1.10
    returns = pd.DataFrame({"A": r})

    result = MaxDrawdown(window_days=3).compute(returns, r.index[-1].date())

    assert result["A"] == pytest.approx(0.88 / 1.10 - 1)


def test_compute_factors_builds_a_ticker_by_factor_table():
    r_a = _returns([0.01, 0.02, 0.01])
    r_b = _returns([-0.01, -0.02, 0.03])
    returns = pd.DataFrame({"A": r_a, "B": r_b})

    table = compute_factors(returns, r_a.index[-1].date(), {"mean": MeanReturn(3), "sharpe": RollingSharpe(3)})

    assert list(table.columns) == ["mean", "sharpe"]
    assert list(table.index) == ["A", "B"]


def test_score_is_a_weighted_sum_of_cross_sectional_zscores():
    table = pd.DataFrame({"x": [1.0, 2.0, 3.0]}, index=["A", "B", "C"])

    result = score(table, {"x": 1.0})

    # z-scores of [1,2,3] are symmetric around 0 -- B (the mean) scores ~0.
    assert result["B"] == pytest.approx(0.0)
    assert result["A"] < result["B"] < result["C"]


def test_score_column_with_zero_variance_contributes_zero_not_nan():
    table = pd.DataFrame({"x": [5.0, 5.0, 5.0]}, index=["A", "B", "C"])

    result = score(table, {"x": 1.0})

    assert (result == 0.0).all()
