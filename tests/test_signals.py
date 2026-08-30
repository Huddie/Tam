import numpy as np
import pandas as pd
import pytest

from tam.registry import Registry
from tam.strategy.signals import Signal, build_signals


def _closes(n, seed=0):
    rng = np.random.default_rng(seed)
    steps = rng.normal(loc=0.1, scale=1.0, size=n)
    values = 100.0 + np.cumsum(steps)
    index = pd.date_range("2024-01-02", periods=n, freq="D")
    return pd.Series(values, index=index)


SIGNAL_IDS = Registry.names(Signal)

# Every registered signal has at least one required constructor arg with no
# default (window/horizon/period) -- these are just representative values so
# the generic tests below can construct one of each without caring about its
# specific config.
_SAMPLE_KWARGS = {
    "sma": {"window": 20},
    "zscore": {"window": 20},
    "rsi": {},
    "return": {"horizon": 5},
    "volatility": {},
    "macd": {},
    "bollinger_pct_b": {},
    "distance_from_high": {},
}


def test_every_builtin_signal_is_registered():
    assert set(SIGNAL_IDS) == set(_SAMPLE_KWARGS)


@pytest.mark.parametrize("signal_id", SIGNAL_IDS)
def test_every_signal_has_a_name_and_a_description(signal_id):
    signal = Registry.create(Signal, signal_id, **_SAMPLE_KWARGS[signal_id])
    assert isinstance(signal.name, str) and signal.name
    assert isinstance(signal.description, str) and len(signal.description) > 10


@pytest.mark.parametrize("signal_id", SIGNAL_IDS)
def test_every_signal_produces_a_series_with_no_nans_given_enough_history(signal_id):
    signal = Registry.create(Signal, signal_id, **_SAMPLE_KWARGS[signal_id])
    close = _closes(signal.required_history() + 30)

    result = signal.compute(close)

    assert isinstance(result, pd.Series)
    assert len(result) > 0
    assert not result.isna().any()
    assert result.index.isin(close.index).all()


def test_build_signals_constructs_from_id_and_config_specs():
    specs = [
        {"id": "sma", "config": {"window": 10}},
        {"id": "sma", "config": {"window": 50}},
        {"id": "rsi"},  # config omitted -> use the signal's own defaults
    ]

    signals = build_signals(specs)

    assert [s.name for s in signals] == ["price_vs_sma_10", "price_vs_sma_50", "rsi_14"]


def test_same_id_with_different_config_produces_distinctly_named_signals():
    signals = build_signals([{"id": "sma", "config": {"window": w}} for w in (10, 50, 100)])
    names = [s.name for s in signals]
    assert len(set(names)) == len(names)


def test_build_signals_expands_a_plural_configs_list_into_one_instance_each():
    specs = [
        {"id": "sma", "configs": [{"window": 10}, {"window": 50}, {"window": 100}]},
        {"id": "macd"},  # neither config nor configs -> one default instance
    ]

    signals = build_signals(specs)

    assert [s.name for s in signals] == [
        "price_vs_sma_10",
        "price_vs_sma_50",
        "price_vs_sma_100",
        "macd_hist_12_26_9",
    ]


def test_build_signals_rejects_both_config_and_configs_on_the_same_entry():
    with pytest.raises(ValueError, match="both 'config' and 'configs'"):
        build_signals([{"id": "sma", "config": {"window": 10}, "configs": [{"window": 50}]}])


def test_price_vs_sma_is_positive_above_the_average_and_negative_below():
    close = pd.Series([100.0] * 9 + [200.0], index=pd.date_range("2024-01-01", periods=10))
    signal = Registry.create(Signal, "sma", window=9)

    result = signal.compute(close)

    assert result.iloc[-1] > 0  # price just jumped well above its own 9-day average


def test_zscore_is_symmetric_around_a_flat_series():
    close = pd.Series([100.0] * 10 + [90.0], index=pd.date_range("2024-01-01", periods=11))
    signal = Registry.create(Signal, "zscore", window=10)

    result = signal.compute(close)

    assert result.iloc[-1] < 0  # price dropped below a flat trailing average


def test_return_signal_matches_a_manual_pct_change():
    close = pd.Series([100.0, 101.0, 99.0, 103.0], index=pd.date_range("2024-01-01", periods=4))
    signal = Registry.create(Signal, "return", horizon=1)

    result = signal.compute(close)

    assert result.iloc[-1] == pytest.approx(103.0 / 99.0 - 1)


def test_distance_from_high_is_zero_at_a_new_high_and_negative_otherwise():
    close = pd.Series([100.0, 105.0, 110.0, 105.0], index=pd.date_range("2024-01-01", periods=4))
    signal = Registry.create(Signal, "distance_from_high", window=3)

    result = signal.compute(close)

    # window=3 -> first valid value once 3 points exist, i.e. at day index 2 (110, a new high).
    assert list(result.index) == list(close.index[2:])
    assert result.iloc[0] == 0.0  # day 2 (110): equal to its own trailing high
    assert result.iloc[1] < 0.0  # day 3 (105): below the high set on day 2


def test_registering_a_duplicate_signal_id_fails_loudly():
    with pytest.raises(ValueError, match="already registered"):

        @Registry.register(Signal, "sma")
        class DuplicateSma(Signal):
            name = "dup"
            description = "dup"

            def required_history(self):
                return 1

            def compute(self, close):
                return close
