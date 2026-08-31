"""Tests for tam.ml.experiment -- run_experiment()'s full wiring
(FeatureStore -> time_split -> Model -> analysis -> ExperimentResult) and
run_sweep()'s cartesian product, against a real (fake-provider-backed)
DataRepository, same pattern tests/test_ml_walk_forward.py already uses."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("torch")
pytest.importorskip("skorch")

from tam.basket.factors import RsiFactor, TrailingReturnFactor  # noqa: E402
from tam.data.providers import DataProvider  # noqa: E402
from tam.data.repository import DataRepository  # noqa: E402
from tam.data.schema import OHLCV_COLUMNS  # noqa: E402
from tam.data.storage import CsvStore  # noqa: E402
from tam.ml.experiment import ExperimentResult, run_experiment, run_sweep  # noqa: E402
from tam.ml.feature_store import FeatureStore  # noqa: E402
from tam.ml.model import MLPModel  # noqa: E402


class _FakeProvider(DataProvider):
    def __init__(self, series: dict):
        self._series = series

    def fetch_eod(self, symbol, start, end):
        dates = pd.date_range(start, end, freq="D")
        closes = self._series[symbol][: len(dates)]
        return pd.DataFrame(
            {
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "adj_close": closes,
                "volume": [100] * len(closes),
            },
            index=dates,
        ).rename_axis("date")[OHLCV_COLUMNS]


def _trending(n, seed, drift=0.05):
    rng = np.random.default_rng(seed)
    return list(100.0 + np.cumsum(rng.normal(loc=drift, scale=1.0, size=n)))


def _store(tmp_path, n=250, n_tickers=6, cache_dir=None):
    tickers = [f"T{i}" for i in range(n_tickers)]
    series = {t: _trending(n, seed=i) for i, t in enumerate(tickers)}
    repo = DataRepository(_FakeProvider(series), CsvStore(tmp_path / "eod"))
    store = FeatureStore(repo, cache_dir=cache_dir)
    store.register_many({"ret_5d": TrailingReturnFactor(5), "rsi_14": RsiFactor(14)})
    return store, tickers, n


_MODEL_KWARGS = {"hidden": 8, "max_epochs": 20, "patience": 5}


def test_run_experiment_returns_a_fully_populated_result(tmp_path):
    store, tickers, n = _store(tmp_path)
    start = date(2024, 1, 1)
    end = start + timedelta(days=n - 1)

    result = run_experiment(store, tickers, start, end, horizon=3, model="mlp", model_kwargs=_MODEL_KWARGS)

    assert isinstance(result, ExperimentResult)
    assert "score" in result.test.columns
    assert result.label_col == "fwd_return_3d"
    assert result.baseline_col == "ret_5d"  # first registered feature, default baseline
    assert isinstance(result.passed_gate, bool)


def test_run_experiment_baseline_col_can_be_overridden(tmp_path):
    store, tickers, n = _store(tmp_path)
    start = date(2024, 1, 1)
    end = start + timedelta(days=n - 1)

    result = run_experiment(
        store, tickers, start, end, horizon=3, model="mlp", model_kwargs=_MODEL_KWARGS, baseline_col="rsi_14"
    )

    assert result.baseline_col == "rsi_14"


def test_run_experiment_accepts_a_model_instance_directly(tmp_path):
    store, tickers, n = _store(tmp_path)
    start = date(2024, 1, 1)
    end = start + timedelta(days=n - 1)
    instance = MLPModel(**_MODEL_KWARGS)

    result = run_experiment(store, tickers, start, end, horizon=3, model=instance)

    assert result.model is instance  # passed straight through, not a separately-constructed copy


def test_passed_gate_is_false_when_model_does_not_beat_baseline(tmp_path):
    store, tickers, n = _store(tmp_path)
    start = date(2024, 1, 1)
    end = start + timedelta(days=n - 1)

    result = run_experiment(store, tickers, start, end, horizon=3, model="mlp", model_kwargs=_MODEL_KWARGS)

    beats_baseline = (
        result.model_ic.mean() > result.baseline_ic.mean()
        and result.model_spread.mean() > result.baseline_spread.mean()
    )
    assert result.passed_gate == beats_baseline


def test_report_prints_a_summary_and_returns_a_composed_chart(tmp_path, capsys):
    store, tickers, n = _store(tmp_path)
    start = date(2024, 1, 1)
    end = start + timedelta(days=n - 1)
    result = run_experiment(store, tickers, start, end, horizon=3, model="mlp", model_kwargs=_MODEL_KWARGS)

    chart = result.report()
    fig = chart.render()

    captured = capsys.readouterr()
    assert "mean IC" in captured.out
    assert "passed_gate" in captured.out
    assert len(fig.data) > 0


def test_run_sweep_produces_one_row_per_grid_combination(tmp_path):
    store, tickers, n = _store(tmp_path)
    start = date(2024, 1, 1)
    end = start + timedelta(days=n - 1)

    leaderboard = run_sweep(
        dict(store=store, tickers=tickers, start=start, end=end, model="mlp", model_kwargs=_MODEL_KWARGS),
        horizon=[2, 3],
    )

    assert len(leaderboard) == 2
    assert set(leaderboard["horizon"]) == {2, 3}
    assert list(leaderboard.columns) == ["horizon", "mean_ic", "mean_spread", "hit_rate", "passed_gate"]


def test_run_sweep_is_sorted_by_mean_ic_descending(tmp_path):
    store, tickers, n = _store(tmp_path)
    start = date(2024, 1, 1)
    end = start + timedelta(days=n - 1)

    leaderboard = run_sweep(
        dict(store=store, tickers=tickers, start=start, end=end, model="mlp", model_kwargs=_MODEL_KWARGS),
        horizon=[1, 2, 3, 5],
    )

    assert list(leaderboard["mean_ic"]) == sorted(leaderboard["mean_ic"], reverse=True)


def test_run_sweep_over_two_grid_axes_produces_the_cartesian_product(tmp_path):
    store, tickers, n = _store(tmp_path, n=300)
    start = date(2024, 1, 1)
    end = start + timedelta(days=n - 1)

    leaderboard = run_sweep(
        dict(store=store, start=start, end=end, model="mlp", model_kwargs=_MODEL_KWARGS),
        horizon=[2, 3],
        tickers=[tickers[:3], tickers],
    )

    assert len(leaderboard) == 4  # 2 horizons x 2 ticker-lists
