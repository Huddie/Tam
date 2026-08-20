import numpy as np
import pandas as pd

from tam.strategy.indicators import sma


def test_sma_matches_manual_rolling_mean():
    values = pd.Series(
        [float(v) for v in range(1, 11)],
        index=pd.date_range("2024-01-01", periods=10),
    )

    result = sma(values, period=3)

    expected = values.rolling(3).mean().dropna()
    assert list(result.index) == list(expected.index)
    assert np.allclose(result.to_numpy(), expected.to_numpy())


def test_sma_output_length_matches_tulipy_convention():
    values = pd.Series(range(5), index=pd.date_range("2024-01-01", periods=5))
    result = sma(values, period=3)
    assert len(result) == len(values) - 3 + 1
    assert result.index[-1] == values.index[-1]
