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


def test_score_penalizes_worse_expected_shortfall_only_with_a_positive_weight():
    # ExpectedShortfall/MaxDrawdown are signed (more negative = worse), the
    # SAME higher-raw-value-is-better direction every other factor here
    # uses -- score() z-scores whatever sign it's given, so a ticker with
    # WORSE (more negative) expected_shortfall must score LOWER only when
    # the weight is POSITIVE. A negative weight does the opposite: it
    # rewards the worse ticker (see ExpectedShortfall's own docstring).
    table = pd.DataFrame({"expected_shortfall": [-0.05, -0.01]}, index=["worse", "better"])

    penalized = score(table, {"expected_shortfall": 0.10})
    assert penalized["worse"] < penalized["better"]

    rewarded = score(table, {"expected_shortfall": -0.10})
    assert rewarded["worse"] > rewarded["better"]


def test_score_fn_is_registered_and_zscore_is_the_default_method():
    from tam.basket.factors import ScoreFn
    from tam.registry import Registry

    assert set(Registry.names(ScoreFn)) >= {"zscore", "rank"}

    table = pd.DataFrame({"x": [1.0, 2.0, 3.0]}, index=["A", "B", "C"])
    assert list(score(table, {"x": 1.0})) == pytest.approx(list(score(table, {"x": 1.0}, method="zscore")))


def test_rank_score_fn_orders_tickers_the_same_as_zscore_for_monotonic_data():
    table = pd.DataFrame({"x": [10.0, 1.0, 1000.0]}, index=["mid", "low", "high"])

    zscore_result = score(table, {"x": 1.0}, method="zscore")
    rank_result = score(table, {"x": 1.0}, method="rank")

    # a single huge outlier dominates the z-score's scale but can only ever
    # occupy the top rank position -- both methods agree on ORDER here...
    assert zscore_result["low"] < zscore_result["mid"] < zscore_result["high"]
    assert rank_result["low"] < rank_result["mid"] < rank_result["high"]
    # ...but rank scores are always bounded to [-0.5, 0.5] regardless of
    # outlier magnitude (rank(pct=True) ranges over (1/n, 1], so this bound
    # is exact at the top and approached, never exceeded, at the bottom),
    # unlike z-scores, which an outlier can stretch arbitrarily wide.
    assert rank_result.max() <= 0.5
    assert rank_result.min() > -0.5


def test_rank_score_fn_centers_scores_around_zero():
    from tam.basket.factors import RankScoreFn

    table = pd.DataFrame({"x": [1.0, 2.0, 3.0]}, index=["A", "B", "C"])

    result = RankScoreFn().compute(table, {"x": 1.0})

    # rank(pct=True) for 3 ascending values is [1/3, 2/3, 1] -- centering by
    # subtracting 0.5 gives an evenly-spaced, but not exactly zero-centered-
    # at-the-median, sequence (that exact symmetry only holds asymptotically
    # for large n): A=1/3-0.5, B=2/3-0.5, C=1-0.5.
    assert result["A"] == pytest.approx(1 / 3 - 0.5)
    assert result["B"] == pytest.approx(2 / 3 - 0.5)
    assert result["C"] == pytest.approx(0.5)


def test_score_raises_a_clear_error_for_an_unregistered_method():
    table = pd.DataFrame({"x": [1.0, 2.0]}, index=["A", "B"])

    with pytest.raises(KeyError, match="not_a_real_method"):
        score(table, {"x": 1.0}, method="not_a_real_method")
