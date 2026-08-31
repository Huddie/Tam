"""Tests for tam.ml.analysis -- information_coefficient/quantile_spread/
hit_rate against small, hand-constructed cross-sections where the correct
answer is known exactly."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tam.ml.analysis import feature_ic_summary, hit_rate, information_coefficient, quantile_spread


def _frame(rows):
    """rows: list of (date, ticker, score, label) -- builds the MultiIndex
    (date, ticker) shape every function here expects."""
    df = pd.DataFrame(rows, columns=["date", "ticker", "score", "label"])
    return df.set_index(["date", "ticker"])


def test_information_coefficient_is_one_for_a_perfectly_monotonic_relationship():
    frame = _frame(
        [
            (date(2024, 1, 1), "A", 1.0, 0.01),
            (date(2024, 1, 1), "B", 2.0, 0.02),
            (date(2024, 1, 1), "C", 3.0, 0.03),
        ]
    )

    result = information_coefficient(frame, "score", "label")

    assert result[date(2024, 1, 1)] == pytest.approx(1.0)


def test_information_coefficient_is_negative_one_for_an_inverse_relationship():
    frame = _frame(
        [
            (date(2024, 1, 1), "A", 1.0, 0.03),
            (date(2024, 1, 1), "B", 2.0, 0.02),
            (date(2024, 1, 1), "C", 3.0, 0.01),
        ]
    )

    result = information_coefficient(frame, "score", "label")

    assert result[date(2024, 1, 1)] == pytest.approx(-1.0)


def test_information_coefficient_is_computed_separately_per_date():
    frame = _frame(
        [
            (date(2024, 1, 1), "A", 1.0, 0.01),
            (date(2024, 1, 1), "B", 2.0, 0.02),
            (date(2024, 1, 2), "A", 1.0, 0.02),
            (date(2024, 1, 2), "B", 2.0, 0.01),
        ]
    )

    result = information_coefficient(frame, "score", "label")

    assert result[date(2024, 1, 1)] == pytest.approx(1.0)
    assert result[date(2024, 1, 2)] == pytest.approx(-1.0)


def test_quantile_spread_is_the_gap_between_top_and_bottom_scored_names():
    frame = _frame(
        [
            (date(2024, 1, 1), "low", 1.0, -0.05),
            (date(2024, 1, 1), "mid", 2.0, 0.0),
            (date(2024, 1, 1), "high", 3.0, 0.05),
        ]
    )

    result = quantile_spread(frame, "score", "label", n_quantiles=3)

    assert result[date(2024, 1, 1)] == pytest.approx(0.05 - (-0.05))


def test_quantile_spread_is_nan_with_too_few_distinct_scores():
    frame = _frame(
        [
            (date(2024, 1, 1), "A", 1.0, 0.01),
            (date(2024, 1, 1), "B", 1.0, 0.02),
        ]
    )

    result = quantile_spread(frame, "score", "label", n_quantiles=5)

    assert np.isnan(result[date(2024, 1, 1)])


def test_hit_rate_counts_matching_signs():
    frame = _frame(
        [
            (date(2024, 1, 1), "A", 1.0, 0.01),  # match (+/+)
            (date(2024, 1, 1), "B", -1.0, -0.01),  # match (-/-)
            (date(2024, 1, 1), "C", 1.0, -0.01),  # mismatch
            (date(2024, 1, 1), "D", -1.0, 0.01),  # mismatch
        ]
    )

    result = hit_rate(frame, "score", "label")

    assert result == pytest.approx(0.5)


def test_hit_rate_is_one_when_every_sign_matches():
    frame = _frame([(date(2024, 1, 1), "A", 1.0, 0.01), (date(2024, 1, 1), "B", -1.0, -0.02)])

    assert hit_rate(frame, "score", "label") == pytest.approx(1.0)


def _multi_feature_frame(rows):
    """rows: list of (date, ticker, good, bad, label) -- `good` moves WITH
    `label`, `bad` moves AGAINST it, both indexed the same way `_frame()`
    builds for the single-column functions above."""
    df = pd.DataFrame(rows, columns=["date", "ticker", "good", "bad", "label"])
    return df.set_index(["date", "ticker"])


def test_feature_ic_summary_ranks_features_by_mean_ic_descending():
    frame = _multi_feature_frame(
        [
            (date(2024, 1, 1), "A", 1.0, 3.0, 0.01),
            (date(2024, 1, 1), "B", 2.0, 2.0, 0.02),
            (date(2024, 1, 1), "C", 3.0, 1.0, 0.03),
        ]
    )

    result = feature_ic_summary(frame, ["good", "bad"], "label")

    assert list(result["feature"]) == ["good", "bad"]
    assert result.loc[0, "mean_ic"] == pytest.approx(1.0)
    assert result.loc[1, "mean_ic"] == pytest.approx(-1.0)


def test_feature_ic_summary_includes_spread_and_hit_rate_columns():
    frame = _multi_feature_frame(
        [
            (date(2024, 1, 1), "A", -1.0, 1.0, -0.05),
            (date(2024, 1, 1), "B", 0.5, -0.5, 0.01),
            (date(2024, 1, 1), "C", 3.0, -3.0, 0.05),
        ]
    )

    result = feature_ic_summary(frame, ["good", "bad"], "label", n_quantiles=3)

    assert list(result.columns) == ["feature", "mean_ic", "mean_spread", "hit_rate"]
    good = result[result["feature"] == "good"].iloc[0]
    bad = result[result["feature"] == "bad"].iloc[0]
    assert good["mean_spread"] == pytest.approx(0.05 - (-0.05))  # top bucket's label minus bottom bucket's
    assert good["hit_rate"] == pytest.approx(1.0)  # good's sign matches label's sign on every row
    assert bad["hit_rate"] == pytest.approx(0.0)  # bad is good's exact negation -- every sign mismatches
