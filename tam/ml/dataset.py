"""time_split: a leakage-safe, time-ordered train/val/test partition of a
FeatureStore.with_labels() dataset.

For a single, fixed 70/15/15-style partition (not k-fold cross-validation),
a small function like this is the right size -- `sklearn.model_selection.
TimeSeriesSplit(n_splits, gap=...)` (confirmed present: sklearn 1.9.0 here
accepts a `gap` parameter) is the well-known basis this borrows the `gap`
idea from, and is the natural upgrade path if this ever needs to become
real expanding-window walk-forward K-fold CV instead of one static split --
but `TimeSeriesSplit` itself produces K folds, not "give me exactly three
named blocks," so it's the wrong shape for what's needed today.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd


def time_split(
    dataset: pd.DataFrame, train_frac: float = 0.70, val_frac: float = 0.85, gap: Optional[int] = None
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Splits `dataset` (a `(date, ticker)`-MultiIndexed frame, e.g. from
    `FeatureStore.with_labels()`) into three time-ordered blocks -- no
    shuffling, since this is a time series. `gap` (trading days) is left
    out of every split boundary so a training-set label window can never
    overlap a validation/test decision date; defaults to
    `dataset.attrs["horizon"]` (set by `FeatureStore.with_labels()`) so the
    leakage gap can't be forgotten by a caller who doesn't pass it
    explicitly."""
    if gap is None:
        gap = dataset.attrs.get("horizon")
        if gap is None:
            raise ValueError(
                "time_split() needs `gap` -- pass it explicitly, or build `dataset` via "
                "FeatureStore.with_labels() so dataset.attrs['horizon'] is set automatically"
            )

    dates = dataset.index.get_level_values("date").unique().sort_values()
    n = len(dates)
    train_end = int(n * train_frac)
    val_end = int(n * val_frac)

    train_dates = dates[:train_end]
    val_dates = dates[train_end + gap : val_end]
    test_dates = dates[val_end + gap :]

    def _slice(dates_subset: pd.Index) -> pd.DataFrame:
        return dataset.loc[dataset.index.get_level_values("date").isin(dates_subset)]

    return _slice(train_dates), _slice(val_dates), _slice(test_dates)
