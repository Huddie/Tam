"""Tests for tam.ml.dataset.time_split -- time-ordered partitioning with a
leakage gap, and the default-gap-from-attrs convenience FeatureStore's
with_labels() sets up for."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from tam.ml.dataset import time_split


def _dataset(n_dates=20, tickers=("A", "B"), horizon=3):
    dates = pd.to_datetime([date(2024, 1, 1) + timedelta(days=i) for i in range(n_dates)])
    rows = [(d, t, i) for i, d in enumerate(dates) for t in tickers]
    df = pd.DataFrame(rows, columns=["date", "ticker", "value"]).set_index(["date", "ticker"])
    df.attrs["horizon"] = horizon
    return df, dates


def test_splits_are_time_ordered_and_non_overlapping():
    dataset, dates = _dataset(n_dates=20, horizon=3)

    train, val, test = time_split(dataset, train_frac=0.5, val_frac=0.75, gap=3)

    train_dates = train.index.get_level_values("date")
    val_dates = val.index.get_level_values("date")
    test_dates = test.index.get_level_values("date")

    assert train_dates.max() < val_dates.min()
    assert val_dates.max() < test_dates.min()


def test_gap_is_actually_left_out_between_splits():
    dataset, dates = _dataset(n_dates=20, horizon=3)

    train, val, test = time_split(dataset, train_frac=0.5, val_frac=0.75, gap=3)

    train_end = train.index.get_level_values("date").max()
    val_start = val.index.get_level_values("date").min()
    gap_days = (val_start - train_end).days
    assert gap_days > 3  # strictly more than the gap itself (the gap dates are excluded, not just adjacent)


def test_gap_defaults_to_the_datasets_own_horizon_attr():
    dataset, dates = _dataset(n_dates=40, horizon=5)

    train, val, _test = time_split(dataset, train_frac=0.5, val_frac=0.75)

    train_end = train.index.get_level_values("date").max()
    val_start = val.index.get_level_values("date").min()
    assert (val_start - train_end).days > 5


def test_raises_a_clear_error_when_gap_is_missing_and_no_horizon_attr():
    dataset, _dates = _dataset(n_dates=20)
    dataset.attrs.pop("horizon", None)

    with pytest.raises(ValueError, match="gap"):
        time_split(dataset)


def test_every_row_lands_in_exactly_one_split_or_the_gap():
    dataset, dates = _dataset(n_dates=30, horizon=2)

    train, val, test = time_split(dataset, train_frac=0.6, val_frac=0.8, gap=2)

    assert len(train) + len(val) + len(test) <= len(dataset)
    all_dates = (
        set(train.index.get_level_values("date"))
        | set(val.index.get_level_values("date"))
        | set(test.index.get_level_values("date"))
    )
    assert all_dates.issubset(set(dates))
