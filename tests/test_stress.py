import pytest

from tam.backtest.stress import flat_shock, stress_test


def test_stress_test_is_the_weighted_sum_of_shocks():
    weights = {"A": 0.04, "B": 0.04, "C": 0.92}

    assert stress_test(weights, {"A": -0.50}) == pytest.approx(-0.02)


def test_stress_test_treats_an_unshocked_ticker_as_zero():
    weights = {"A": 0.5, "B": 0.5}

    assert stress_test(weights, {"A": -0.20}) == pytest.approx(0.5 * -0.20)


def test_concentration_makes_the_same_single_name_shock_worse():
    small_position = stress_test({"A": 0.04, "B": 0.96}, {"A": -0.50})
    large_position = stress_test({"A": 0.20, "B": 0.80}, {"A": -0.50})

    assert large_position < small_position


def test_flat_shock_applies_the_same_magnitude_to_every_weighted_ticker():
    weights = {"A": 0.3, "B": 0.7}

    shocks = flat_shock(weights, -0.05)

    assert shocks == {"A": -0.05, "B": -0.05}
    assert stress_test(weights, shocks) == pytest.approx(-0.05)
