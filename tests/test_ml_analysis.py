"""Tests for tam.ml.analysis -- information_coefficient/quantile_spread/
hit_rate against small, hand-constructed cross-sections where the correct
answer is known exactly."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tam.ml.analysis import hit_rate, information_coefficient, quantile_spread


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
