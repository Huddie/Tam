from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tam.basket.factors import (
    CrossSectionalRank,
    ExpectedShortfall,
    MacdFactor,
    MaxDrawdown,
    MeanReturn,
    OvernightAlpha,
    OvernightBeta,
    Persistence,
    RealizedVolFactor,
    RollingSharpe,
    RsiFactor,
    SmaDistanceFactor,
    TrailingReturnFactor,
    compute_factors,
    score,
)


def _prices(values, start=date(2024, 1, 1)):
    idx = pd.to_datetime([start + timedelta(days=i) for i in range(len(values))])
    return pd.Series(values, index=idx)


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


def test_trailing_return_factor_matches_hand_computed_cumulative_return():
    p = _prices([100.0, 101.0, 99.0, 102.0, 105.0, 110.0])
    prices = pd.DataFrame({"A": p})

    result = TrailingReturnFactor(window_days=5).compute(prices, p.index[-1].date())

    assert result["A"] == pytest.approx(110.0 / 100.0 - 1)


def test_trailing_return_factor_is_zero_with_insufficient_history():
    p = _prices([100.0, 101.0])
    prices = pd.DataFrame({"A": p})

    result = TrailingReturnFactor(window_days=5).compute(prices, p.index[-1].date())

    assert result["A"] == 0.0


def test_rsi_factor_never_sees_data_after_as_of():
    rng = np.random.default_rng(3)
    p = _prices(100 * np.cumprod(1 + rng.normal(0, 0.01, 60)))
    prices = pd.DataFrame({"A": p})
    as_of = p.index[40].date()

    before = RsiFactor(14).compute(prices, as_of)
    corrupted = prices.copy()
    corrupted.loc[corrupted.index > pd.Timestamp(as_of)] = 999.0
    after = RsiFactor(14).compute(corrupted, as_of)

    assert before.equals(after)


def test_rsi_factor_is_neutral_with_insufficient_history():
    p = _prices([100.0, 101.0])
    prices = pd.DataFrame({"A": p})

    result = RsiFactor(14).compute(prices, p.index[-1].date())

    assert result["A"] == 50.0


def test_rsi_factor_bounded_window_is_close_to_unbounded_full_history():
    # The bounded-window optimization (period * 5) is a deliberate
    # approximation, not an exact match -- Wilder's smoothing technically
    # depends on where the series starts, so a shorter window converges to
    # a very close but not bit-identical value. That's the whole point
    # (bounding trades a negligible accuracy difference for avoiding the
    # O(n^2) blowup confirmed live against the full-history version).
    rng = np.random.default_rng(4)
    p = _prices(100 * np.cumprod(1 + rng.normal(0, 0.01, 300)))
    prices = pd.DataFrame({"A": p})
    as_of = p.index[-1].date()

    from tam.strategy.indicators import rsi

    bounded = RsiFactor(14).compute(prices, as_of)
    full_history = rsi(prices["A"], 14).iloc[-1]

    assert bounded["A"] == pytest.approx(full_history, abs=2.0)


def test_macd_factor_is_zero_with_insufficient_history():
    p = _prices([100.0, 101.0, 99.0])
    prices = pd.DataFrame({"A": p})

    result = MacdFactor().compute(prices, p.index[-1].date())

    assert result["A"] == 0.0


def test_macd_factor_matches_indicators_module_normalized_by_price():
    rng = np.random.default_rng(5)
    p = _prices(100 * np.cumprod(1 + rng.normal(0, 0.01, 60)))
    prices = pd.DataFrame({"A": p})
    as_of = p.index[-1].date()

    from tam.strategy.indicators import macd_histogram

    result = MacdFactor().compute(prices, as_of)
    expected = macd_histogram(p, 12, 26, 9).iloc[-1] / p.iloc[-1]

    assert result["A"] == pytest.approx(expected)


def test_realized_vol_factor_matches_hand_computed_annualized_std():
    p = _prices([100.0, 101.0, 99.0, 102.0, 98.0])
    prices = pd.DataFrame({"A": p})

    result = RealizedVolFactor(window_days=4).compute(prices, p.index[-1].date())

    expected = p.pct_change().std() * (252**0.5)
    assert result["A"] == pytest.approx(expected)


def test_sma_distance_factor_matches_hand_computed_value():
    from tam.strategy.indicators import sma

    p = _prices([100.0, 101.0, 99.0, 102.0, 105.0])
    prices = pd.DataFrame({"A": p})
    as_of = p.index[-1].date()

    result = SmaDistanceFactor(window_days=5).compute(prices, as_of)

    expected = p.iloc[-1] / sma(p, 5).iloc[-1] - 1
    assert result["A"] == pytest.approx(expected)


def test_cross_sectional_rank_reranks_the_wrapped_factors_output():
    idx = pd.to_datetime([date(2024, 1, 1) + timedelta(days=i) for i in range(6)])
    prices = pd.DataFrame(
        {
            "low": [100.0, 100.0, 100.0, 100.0, 100.0, 101.0],  # +1%
            "mid": [100.0, 100.0, 100.0, 100.0, 100.0, 105.0],  # +5%
            "high": [100.0, 100.0, 100.0, 100.0, 100.0, 110.0],  # +10%
        },
        index=idx,
    )

    result = CrossSectionalRank(TrailingReturnFactor(window_days=5)).compute(prices, idx[-1].date())

    assert result["low"] < result["mid"] < result["high"]
    assert result.max() <= 0.5
    assert result.min() > -0.5


def test_price_based_factors_are_usable_together_via_compute_factors():
    rng = np.random.default_rng(6)
    p_a = _prices(100 * np.cumprod(1 + rng.normal(0, 0.01, 60)))
    p_b = _prices(50 * np.cumprod(1 + rng.normal(0, 0.02, 60)))
    prices = pd.DataFrame({"A": p_a, "B": p_b})

    table = compute_factors(
        prices,
        p_a.index[-1].date(),
        {
            "ret_5d": TrailingReturnFactor(5),
            "rsi_14": RsiFactor(14),
            "macd": MacdFactor(),
            "vol_10d": RealizedVolFactor(10),
            "sma_dist_20": SmaDistanceFactor(20),
            "rsi_14_xrank": CrossSectionalRank(RsiFactor(14)),
        },
    )

    assert list(table.columns) == ["ret_5d", "rsi_14", "macd", "vol_10d", "sma_dist_20", "rsi_14_xrank"]
    assert list(table.index) == ["A", "B"]
    assert not table.isna().any().any()


def test_intraday_volatility_factor_reads_from_the_registered_minute_bar_source(monkeypatch):
    from tam.basket.factors import IntradayVolatilityFactor
    from tam.marketdata.minute_source import MinuteBarSource
    from tam.registry import Registry

    class _FakeMinuteBarSource:
        def fetch_minute_bars(self, symbol, start, end, engine="pandas"):
            idx = pd.date_range("2024-01-02 14:30", periods=5, freq="1min", tz="UTC")
            scale = {"A": 0.001, "B": 0.05}[symbol]
            closes = 100 * (1 + np.array([0, 1, -1, 1, -1]) * scale)
            return pd.DataFrame({"close": closes}, index=idx)

    monkeypatch.setitem(Registry._singletons, (MinuteBarSource, "marketdata"), _FakeMinuteBarSource())

    prices = pd.DataFrame({"A": [0.0], "B": [0.0]})  # only .columns matters here
    result = IntradayVolatilityFactor().compute(prices, date(2024, 1, 2))

    assert result["A"] < result["B"]
