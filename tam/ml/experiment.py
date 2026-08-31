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
from .analysis import feature_ic_summary, hit_rate, information_coefficient, quantile_spread
from .dataset import time_split
from .feature_store import FeatureStore
from .model import Model

# A commonly-cited rule of thumb in cross-sectional equity research: mean IC
# below ~0.02 is weak even when it's positive, 0.05+ starts to look like a
# real signal. Not a hard boundary -- just enough to flag "passed_gate=True
# doesn't mean strong" directly in report(), instead of leaving that
# distinction for the reader to infer from a bare number.
_WEAK_IC_THRESHOLD = 0.02


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

    @property
    def feature_cols(self) -> list[str]:
        """Every registered feature's own column name -- `self.test` minus
        `label_col` and the model's own `"score"` column, in the order they
        already appear (the same list `run_experiment()` itself trained
        on)."""
        return [c for c in self.test.columns if c not in (self.label_col, "score")]

    def report(self):
        """Prints a full model-vs-baseline comparison plus a per-feature
        leaderboard, and returns a five-panel report: a numeric summary
        table, the per-feature IC/spread/hit-rate leaderboard (every
        registered feature scored the SAME way as the model, independently
        -- which of them already carries signal alone, or comes close to
        matching the model), each feature's own IC over time (click a
        legend entry to isolate/hide it -- Plotly's own built-in
        interaction, not a separate filter widget), a feature correlation
        heatmap (redundant features cluster near +-1; a model built on
        several near-duplicates isn't actually using as much independent
        information as its feature count suggests), and the model/baseline
        score distributions. Call `.show()` on the result in a notebook, or
        let it auto-display as the cell's last expression."""
        from ..charting import distribution, heatmap, table, timeseries

        feature_cols = self.feature_cols
        leaderboard = feature_ic_summary(self.test, [*feature_cols, "score"], self.label_col)
        leaderboard["feature"] = leaderboard["feature"].replace({"score": "model_score"})

        print(
            f"model    -- mean IC: {self.model_ic.mean():.4f}  "
            f"mean spread: {self.model_spread.mean():.5f}  hit rate: {self.model_hit_rate:.4f}"
        )
        print(
            f"baseline -- mean IC: {self.baseline_ic.mean():.4f}  "
            f"mean spread: {self.baseline_spread.mean():.5f}  hit rate: {self.baseline_hit_rate:.4f}"
        )
        print(f"passed_gate: {self.passed_gate}  (beats {self.baseline_col!r} on both mean IC and mean spread)")
        if abs(self.model_ic.mean()) < _WEAK_IC_THRESHOLD:
            print(
                f"note: mean IC ({self.model_ic.mean():.4f}) is below the commonly-cited ~{_WEAK_IC_THRESHOLD} "
                "weak-signal line -- passed_gate means 'better than this one baseline,' not 'strong' or 'ready to trade.'"
            )
        print("\nper-feature leaderboard -- every registered feature + the model's own score, ranked by mean IC:")
        print(leaderboard.round(4).to_string(index=False))

        summary_table = table(
            pd.DataFrame(
                {
                    "": ["model", "baseline"],
                    "mean_ic": [self.model_ic.mean(), self.baseline_ic.mean()],
                    "mean_spread": [self.model_spread.mean(), self.baseline_spread.mean()],
                    "hit_rate": [self.model_hit_rate, self.baseline_hit_rate],
                }
            ),
            title="Model vs. baseline",
        )
        leaderboard_table = table(leaderboard, title="Per-feature leaderboard")

        feature_ic_over_time = {col: information_coefficient(self.test, col, self.label_col) for col in feature_cols}
        feature_ic_over_time["model_score"] = self.model_ic
        ic_chart = timeseries(feature_ic_over_time, title="IC over time -- click a legend entry to isolate/hide it")

        corr_chart = heatmap(self.test[feature_cols].corr(), title="Feature correlations")

        score_chart = distribution(
            {"model": self.test["score"], "baseline": self.test[self.baseline_col]},
            title="Score distribution, test period",
        )

        return summary_table | leaderboard_table | ic_chart | corr_chart | score_chart


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
