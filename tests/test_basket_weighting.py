import pandas as pd
import pytest

from tam.basket.weighting import inverse_vol_weights


def test_weights_are_inversely_proportional_to_volatility():
    scores = pd.Series({"A": 1.0, "B": 1.0})
    vol = pd.Series({"A": 0.01, "B": 0.02})

    weights = inverse_vol_weights(scores, vol)

    # Equal score, half the vol -> twice the weight.
    assert weights["A"] == pytest.approx(weights["B"] * 2)
    assert weights.sum() == pytest.approx(1.0)


def test_negative_scores_get_zero_weight_long_only():
    scores = pd.Series({"A": 5.0, "B": -3.0})
    vol = pd.Series({"A": 0.01, "B": 0.01})

    weights = inverse_vol_weights(scores, vol)

    assert weights["B"] == 0.0
    assert weights["A"] == pytest.approx(1.0)


def test_all_negative_scores_returns_all_zero_not_a_crash():
    scores = pd.Series({"A": -1.0, "B": -2.0})
    vol = pd.Series({"A": 0.01, "B": 0.01})

    weights = inverse_vol_weights(scores, vol)

    assert (weights == 0.0).all()


def test_max_weight_cap_is_enforced_and_excess_redistributed():
    scores = pd.Series({"A": 10.0, "B": 1.0, "C": 1.0, "D": 1.0})
    vol = pd.Series({"A": 0.001, "B": 0.01, "C": 0.01, "D": 0.01})

    weights = inverse_vol_weights(scores, vol, max_weight=0.4)

    assert weights["A"] == pytest.approx(0.4)
    assert weights.sum() == pytest.approx(1.0)
    assert (weights <= 0.4 + 1e-9).all()


def test_sector_cap_limits_total_sector_weight_and_redistributes_the_rest():
    scores = pd.Series({"A": 5.0, "B": 5.0, "C": 1.0, "D": 1.0})
    vol = pd.Series({"A": 0.01, "B": 0.01, "C": 0.01, "D": 0.01})
    sectors = pd.Series({"A": "tech", "B": "tech", "C": "fin", "D": "fin"})

    weights = inverse_vol_weights(scores, vol, max_weight=1.0, sector_caps={"tech": 0.3}, sectors=sectors)

    assert weights[["A", "B"]].sum() == pytest.approx(0.3)
    assert weights.sum() == pytest.approx(1.0)


def test_zero_volatility_does_not_crash_and_contributes_no_weight():
    scores = pd.Series({"A": 5.0, "B": 5.0})
    vol = pd.Series({"A": 0.0, "B": 0.01})

    weights = inverse_vol_weights(scores, vol)

    assert weights["A"] == 0.0
    assert weights["B"] == pytest.approx(1.0)
