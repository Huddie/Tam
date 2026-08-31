"""run_experiment()/run_sweep(): the one-call entry points tying
FeatureStore, time_split, Model, and analysis together -- "the meat" a
developer needs to supply (which factors are already in the FeatureStore,
which model) is everything these two functions DON'T do.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Optional, Union

import pandas as pd

from ..registry import Registry
from .analysis import hit_rate, information_coefficient, quantile_spread
from .dataset import time_split
from .feature_store import FeatureStore
from .model import Model


@dataclass
class ExperimentResult:
    """Everything from one `run_experiment()` call: the fitted model, the
    scored test set, and the model-vs-baseline comparison the gate (step 7
    of the original Colab guide, now enforced here) is based on."""

    config: dict[str, Any]
    model: Model
    test: pd.DataFrame
    label_col: str
    baseline_col: str
    model_ic: pd.Series
    model_spread: pd.Series
    model_hit_rate: float
    baseline_ic: pd.Series
    baseline_spread: pd.Series
    baseline_hit_rate: float

    @property
    def passed_gate(self) -> bool:
        """True only if the model beats the naive baseline on BOTH mean IC
        and mean quantile spread -- the same rule the original guide's
        step 7 stated in prose, now a real, checkable property instead of
        a docs callout someone has to remember to apply by eye."""
        return bool(
            self.model_ic.mean() > self.baseline_ic.mean() and self.model_spread.mean() > self.baseline_spread.mean()
        )

    def report(self):
        """Prints the model-vs-baseline comparison and returns a composed
        chart (IC over time + score distributions) -- call `.show()` on the
        result in a notebook, or let it auto-display as the cell's last
        expression."""
        from ..charting import distribution, timeseries

        print(
            f"model    -- mean IC: {self.model_ic.mean():.4f}  "
            f"mean spread: {self.model_spread.mean():.5f}  hit rate: {self.model_hit_rate:.4f}"
        )
        print(
            f"baseline -- mean IC: {self.baseline_ic.mean():.4f}  "
            f"mean spread: {self.baseline_spread.mean():.5f}  hit rate: {self.baseline_hit_rate:.4f}"
        )
        print(f"passed_gate: {self.passed_gate}")

        ic_chart = timeseries(
            {"model_ic": self.model_ic, "baseline_ic": self.baseline_ic}, title="Information coefficient, test period"
        )
        score_chart = distribution(
            {"model": self.test["score"], "baseline": self.test[self.baseline_col]},
            title="Score distribution, test period",
        )
        return ic_chart | score_chart


def run_experiment(
    store: FeatureStore,
    tickers,
    start: date,
    end: date,
    horizon: int,
    model: Union[str, Model],
    model_kwargs: Optional[dict] = None,
    baseline_col: Optional[str] = None,
    train_frac: float = 0.70,
    val_frac: float = 0.85,
    warmup: int = 60,
) -> ExperimentResult:
    """`store.with_labels(...)` -> `time_split` -> fit `model` -> score the
    test split -> `ExperimentResult`. `model` is either a registered
    `Registry(Model, ...)` name (`model_kwargs` construct it) or an
    already-built `Model` instance (`model_kwargs` ignored -- it's already
    constructed) -- pass an instance directly to try an architecture without
    registering it first. `baseline_col` defaults to `store`'s first
    registered feature name (any raw Factor value is a legitimate naive
    baseline) if not given."""
    dataset = store.with_labels(tickers, start, end, horizon, warmup=warmup)
    label_col = dataset.attrs["label_col"]
    feature_cols = store.feature_names
    baseline_col = baseline_col or feature_cols[0]

    train, val, test = time_split(dataset, train_frac=train_frac, val_frac=val_frac, gap=horizon)

    model_instance = Registry.resolve(Model, model, **(model_kwargs or {}))
    model_instance.fit(
        train[feature_cols].to_numpy(),
        train[label_col].to_numpy(),
        val[feature_cols].to_numpy(),
        val[label_col].to_numpy(),
    )

    test = test.copy()
    test["score"] = model_instance.predict(test[feature_cols].to_numpy())

    return ExperimentResult(
        config={
            "tickers": list(tickers),
            "start": start,
            "end": end,
            "horizon": horizon,
            "model": model,
            "model_kwargs": model_kwargs,
        },
        model=model_instance,
        test=test,
        label_col=label_col,
        baseline_col=baseline_col,
        model_ic=information_coefficient(test, "score", label_col),
        model_spread=quantile_spread(test, "score", label_col),
        model_hit_rate=hit_rate(test, "score", label_col),
        baseline_ic=information_coefficient(test, baseline_col, label_col),
        baseline_spread=quantile_spread(test, baseline_col, label_col),
        baseline_hit_rate=hit_rate(test, baseline_col, label_col),
    )


def run_sweep(base_kwargs: dict[str, Any], **grids) -> pd.DataFrame:
    """Cartesian product over `grids` (e.g. `horizon=[1,2,3,5]`, or
    `tickers=[universe[:30], universe[:100]]`) via
    `sklearn.model_selection.ParameterGrid` -- one `run_experiment()` call
    per combination, returned as a leaderboard sorted by mean IC. Doesn't
    resolve tickers/universes/feature-sets itself -- a `tickers=[...]` or
    `store=[...]` grid axis is just a list of already-built objects."""
    from sklearn.model_selection import ParameterGrid

    rows = []
    for combo in ParameterGrid(grids):
        result = run_experiment(**{**base_kwargs, **combo})
        rows.append(
            {
                **{key: value for key, value in combo.items() if key != "store"},
                "mean_ic": result.model_ic.mean(),
                "mean_spread": result.model_spread.mean(),
                "hit_rate": result.model_hit_rate,
                "passed_gate": result.passed_gate,
            }
        )
    return pd.DataFrame(rows).sort_values("mean_ic", ascending=False).reset_index(drop=True)
