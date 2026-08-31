"""Signal-quality analysis: pure functions over a model's cross-sectional
scores + realized forward returns -- no model/strategy coupling, so they
work identically for a trained Model's output and for a naive baseline
(e.g. a raw Factor's own value) compared against it.

Kept as small custom functions rather than adopting `alphalens-reloaded`
(this session's build-vs-buy check found it downgrades pandas and drags in
a large, mostly-redundant matplotlib/seaborn/statsmodels stack for
functionality each of these is 3-10 lines over already-installed
pandas/numpy) -- see docs/ml.md for the full reasoning.

`frame` throughout is expected to have a MultiIndex (`date`, `ticker`) --
exactly what `FeatureStore.with_labels()` produces.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def information_coefficient(frame: pd.DataFrame, score_col: str, label_col: str) -> pd.Series:
    """Per-date Spearman rank correlation between `score_col` and
    `label_col` -- the standard cross-sectional signal-quality metric.
    NaN for any date with fewer than 2 tickers or zero variance in either
    column (nothing to correlate)."""
    return frame.groupby(level="date").apply(lambda g: g[score_col].corr(g[label_col], method="spearman"))


def quantile_spread(frame: pd.DataFrame, score_col: str, label_col: str, n_quantiles: int = 5) -> pd.Series:
    """Mean `label_col` of the top `score_col`-quantile minus the bottom
    one, per date -- NaN for a date with fewer distinct score values than
    `n_quantiles` (can't form that many buckets)."""

    def _spread(group: pd.DataFrame) -> float:
        if group[score_col].nunique() < n_quantiles:
            return float("nan")
        bucket = pd.qcut(group[score_col], n_quantiles, labels=False, duplicates="drop")
        top = group.loc[bucket == bucket.max(), label_col].mean()
        bottom = group.loc[bucket == bucket.min(), label_col].mean()
        return top - bottom

    return frame.groupby(level="date").apply(_spread)


def hit_rate(frame: pd.DataFrame, score_col: str, label_col: str) -> float:
    """Fraction of rows where `score_col` and `label_col` have the same
    sign -- the simplest possible "did the direction call work" metric."""
    return float((np.sign(frame[score_col]) == np.sign(frame[label_col])).mean())
