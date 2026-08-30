from datetime import date, timedelta

import numpy as np
import pandas as pd

from tam.basket.selection import cluster, select_diversified


def _index(periods, start=date(2022, 1, 1)):
    return pd.to_datetime([start + timedelta(days=i) for i in range(periods)])


def test_cluster_separates_two_distinct_correlation_groups():
    rng = np.random.default_rng(0)
    idx = _index(300)
    factor_a = rng.normal(0, 0.01, 300)
    factor_b = rng.normal(0, 0.01, 300)

    def noise():
        return rng.normal(0, 0.002, 300)

    returns = pd.DataFrame(
        {
            "NVDA": factor_a + noise(),
            "AMD": factor_a + noise(),
            "JPM": factor_b + noise(),
            "BAC": factor_b + noise(),
        },
        index=idx,
    )

    clusters = cluster(returns, n_clusters=2)

    assert clusters["NVDA"] == clusters["AMD"]
    assert clusters["JPM"] == clusters["BAC"]
    assert clusters["NVDA"] != clusters["JPM"]


def test_cluster_handles_a_single_ticker_without_crashing():
    returns = pd.DataFrame({"A": [0.01, 0.02, -0.01]})

    clusters = cluster(returns, n_clusters=3)

    assert list(clusters.index) == ["A"]


def test_cluster_caps_n_clusters_at_the_number_of_tickers():
    returns = pd.DataFrame({"A": [0.01, 0.02, -0.01], "B": [0.02, 0.01, -0.02]})

    clusters = cluster(returns, n_clusters=10)  # more clusters than tickers

    assert set(clusters.index) == {"A", "B"}


def test_downside_quantile_restricts_correlation_to_bad_days():
    # A and B are anti-correlated on normal days but crash together on bad
    # days; C and D are just noise, uncorrelated with anything, on both.
    idx = _index(20)
    market = pd.Series([0.01] * 15 + [-0.09] * 5, index=idx)
    normal_a = [0.010, 0.015, 0.008, 0.012, 0.011, 0.009, 0.013, 0.010, 0.014, 0.007, 0.012, 0.010, 0.011, 0.009, 0.013]
    normal_b = [
        -0.010,
        -0.008,
        -0.015,
        -0.009,
        -0.012,
        -0.011,
        -0.007,
        -0.013,
        -0.010,
        -0.014,
        -0.009,
        -0.012,
        -0.010,
        -0.011,
        -0.008,
    ]
    crash_a = [-0.080, -0.090, -0.070, -0.085, -0.088]
    crash_b = [-0.079, -0.091, -0.069, -0.086, -0.089]
    normal_c = [
        0.005,
        -0.003,
        0.002,
        -0.004,
        0.006,
        -0.002,
        0.003,
        -0.005,
        0.004,
        -0.003,
        0.002,
        -0.004,
        0.005,
        -0.002,
        0.003,
    ]
    normal_d = [
        -0.004,
        0.002,
        -0.003,
        0.005,
        -0.002,
        0.004,
        -0.003,
        0.002,
        -0.005,
        0.004,
        -0.002,
        0.003,
        -0.004,
        0.002,
        -0.003,
    ]
    crash_c = [0.010, -0.020, 0.015, -0.010, 0.005]
    crash_d = [-0.010, 0.020, -0.015, 0.010, -0.005]
    returns = pd.DataFrame(
        {
            "A": normal_a + crash_a,
            "B": normal_b + crash_b,
            "C": normal_c + crash_c,
            "D": normal_d + crash_d,
        },
        index=idx,
    )

    downside_clusters = cluster(returns, n_clusters=2, downside_quantile=0.25, market_returns=market)

    # On the worst days both A and B crash together -> same cluster, even
    # though they're anti-correlated across the full sample.
    assert downside_clusters["A"] == downside_clusters["B"]


def test_select_diversified_caps_picks_per_cluster():
    scores = pd.Series({"NVDA": 5, "AMD": 4, "MU": 3, "JPM": 2, "BAC": 1})
    clusters = pd.Series({"NVDA": 0, "AMD": 0, "MU": 0, "JPM": 1, "BAC": 1})

    picks = select_diversified(scores, clusters, n=3, max_per_cluster=1)

    assert picks == ["NVDA", "JPM"]  # MU skipped: cluster 0 already had its 1 pick


def test_select_diversified_without_a_cap_is_plain_top_n():
    scores = pd.Series({"NVDA": 5, "AMD": 4, "MU": 3, "JPM": 2})
    clusters = pd.Series({"NVDA": 0, "AMD": 0, "MU": 0, "JPM": 1})

    picks = select_diversified(scores, clusters, n=3, max_per_cluster=3)

    assert picks == ["NVDA", "AMD", "MU"]


def test_select_diversified_stops_at_n_even_with_room_left_in_other_clusters():
    scores = pd.Series({"A": 3, "B": 2, "C": 1})
    clusters = pd.Series({"A": 0, "B": 1, "C": 2})

    picks = select_diversified(scores, clusters, n=2, max_per_cluster=5)

    assert picks == ["A", "B"]
