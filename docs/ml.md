# ML

*Full generated reference: [`tam.ml`](api/tam.ml.rst).*

A research harness for building/testing an ML-based trading signal:
`FeatureStore` (a named, point-in-time-safe feature registry + a cache),
`Model` (pluggable architectures, `Registry`-backed, built on `skorch`),
leakage-safe `time_split()`, IC/quantile-spread/hit-rate analysis, and
`run_experiment()`/`run_sweep()` tying them together. Like
[`tam.basket`](basket.md), this is a research *toolkit*, not one fixed
strategy — pull each piece you need, compare candidate configs, and only
turn a promising result into a real [`Strategy`](strategy.md) once
`run_experiment()`'s gate actually passes.

```bash
pip install "tam-quant[ml]"
```

```python
from datetime import date, timedelta

from tam.basket.factors import MacdFactor, RsiFactor, TrailingReturnFactor
from tam.basket.universe import PitIndexUniverse
from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.storage import DataStore
from tam.marketdata.eod_provider import MarketDataEodProvider  # noqa: F401 -- registers "marketdata_eod"
from tam.ml.experiment import run_experiment, run_sweep
from tam.ml.feature_store import FeatureStore
from tam.registry import Registry

universe = PitIndexUniverse(index="sp500")
tickers = universe.constituents(date.today())[:30]

repository = DataRepository(Registry.get(DataProvider, "marketdata_eod"), Registry.create(DataStore, "parquet", "data/eod"))

# 1. Register the features that matter -- Factor + Registry ARE the feature-
#    store abstraction here (a named, point-in-time-safe registry); FeatureStore
#    adds materialization + a Parquet cache on top, not a second concept.
store = FeatureStore(repository, cache_dir="feature_cache")
store.register_many({"ret_5d": TrailingReturnFactor(5), "rsi_14": RsiFactor(14), "macd": MacdFactor()})

# 2. Train + evaluate one config -- everything that isn't "which factors"/
#    "which architecture" (panel construction, the leakage gap, standardization,
#    the training loop, IC/quantile-spread/hit-rate vs. a naive baseline) is
#    handled here, not hand-rolled per experiment.
end = date.today()
start = end - timedelta(days=365 * 6)
result = run_experiment(store, tickers, start, end, horizon=3, model="mlp", model_kwargs={"hidden": 32})
result.report()  # prints the comparison, returns an IC-over-time + score-distribution chart
result.passed_gate  # bool: model beats a naive baseline on both mean IC and mean quantile spread

# 3. Iterate -- a grid over configs, not five more notebook cells per idea.
leaderboard = run_sweep(
    dict(store=store, start=start, end=end, model="mlp", model_kwargs={"hidden": 32}),
    horizon=[1, 2, 3, 5],
)
```

## Why not adopt a feature-store/experiment-tracking package outright

Checked, not assumed, against this project's actual scale (a single
researcher, a few dozen-to-hundred tickers, notebook-driven iteration):

- **Feast/Tecton/Hopsworks** (feature stores): rejected. Their real
  value-add — point-in-time-correct historical feature retrieval — is
  exactly what `Factor._window()`/`compute_factors()` already provides
  (see [Basket research](basket.md)). Adopting one means either rewriting
  every existing `Factor` into an incompatible DSL (`FeatureView`/`Entity`,
  a `feature_store.yaml`), or running two parallel feature-definition
  systems side by side. Both confirmed (via dry-run dependency resolution)
  to pull a full local web-server stack even "for local use."
- **`alphalens-reloaded`** (factor/IC analysis): rejected. Purpose-built
  for exactly this analysis, but a dry-run install shows it downgrading
  `pandas` and dragging in a large, mostly-redundant matplotlib/seaborn/
  statsmodels stack that duplicates `tam.charting`'s own Plotly-based
  charts — not worth it for functionality (`information_coefficient`/
  `quantile_spread`/`hit_rate`) that's each 3-10 lines over already-
  installed `pandas`/`numpy`.
- **`skorch`** (model fit/predict/early-stopping/checkpointing): **adopted**.
  A thin, well-maintained, sklearn-compatible wrapper around a plain
  `nn.Module` — confirmed small (only pulls `tabulate`/`tqdm`
  transitively). `SkorchModel` is built on it rather than a hand-rolled
  training loop; "the meat" a new architecture needs to supply shrinks to
  one `_build_module(n_features) -> nn.Module` method.
- **`sklearn.preprocessing.StandardScaler`**: adopted for `SkorchModel`'s
  standardization — already a hard dependency, already used elsewhere in
  this codebase (`tam/strategy/ml_walk_forward.py`), not a new pattern.
- **`sklearn.model_selection.ParameterGrid`**: adopted for `run_sweep()`'s
  cartesian product, for the same reason — already available, no reason to
  hand-roll `itertools.product`.
- **MLflow** (experiment tracking): not adopted, but a documented future
  option once there are dozens of sweep runs to compare rather than one
  small grid — a dry-run install shows it pulling a full local web-server
  stack (`starlette`/`uvicorn`) even for file-based local tracking, real
  overkill for a `pd.DataFrame` leaderboard's current scale.
- **Optuna** (hyperparameter search beyond a grid): not adopted, but the
  well-known next step once `run_sweep()`'s grid search stops being enough
  — confirmed resolvable with a reasonable dependency footprint.

## `FeatureStore`

```python
from tam.ml.feature_store import FeatureStore

store = FeatureStore(repository, cache_dir="feature_cache")  # cache_dir optional -- omit to skip caching entirely
store.register("rsi_14", RsiFactor(14))  # chainable
store.register_many({"ret_5d": TrailingReturnFactor(5), "macd": MacdFactor()})
store.feature_names  # ["rsi_14", "ret_5d", "macd"]

panel = store.materialize(tickers, start, end, warmup=60)  # (date, ticker) x feature
dataset = store.with_labels(tickers, start, end, horizon=3)  # + forward-3-day-return label, ready for time_split()
```

`materialize()`/`with_labels()` compute internally in pandas regardless of
`engine=` (`Factor`/`compute_factors`/`price_matrix` are pandas end-to-end
— their own contract, not rewritten here); pass `engine="polars"` to
convert only at the return boundary, the identical pattern
`Symbol.financials()` already uses for the same reason. `cache_dir`, if
given, memoizes the computed panel to a Parquet file keyed by a hash of
`(tickers, start, end, horizon, each registered Factor's own config)` — a
cache hit skips recomputation entirely, the same "only do the work that's
actually missing" shape `DataRepository.ingest()` already uses.

## `time_split()`

```python
from tam.ml.dataset import time_split

train, val, test = time_split(dataset, train_frac=0.70, val_frac=0.85, gap=3)
```

Time-ordered (no shuffling — this is a time series), with `gap` (trading
days) left out of every split boundary so a training-set label window can
never overlap a validation/test decision date. `gap` defaults to
`dataset.attrs["horizon"]` (set automatically by `FeatureStore.with_labels()`)
so the leakage gap can't be forgotten by a caller who doesn't pass it
explicitly. `sklearn.model_selection.TimeSeriesSplit(n_splits, gap=...)` is
the well-known basis this borrows the `gap` idea from, and the natural
upgrade path if this ever needs to become real expanding-window walk-forward
K-fold CV instead of one static 3-way split — but it produces K folds, not
"give me exactly three named blocks," so it's the wrong shape for that.

## `Model` — pluggable architectures

```python
from tam.registry import Registry
from tam.ml.model import Model, SkorchModel
import torch.nn as nn


@Registry.register(Model, "my_mlp")
class MyMLP(SkorchModel):
    def _build_module(self, n_features):
        return nn.Sequential(nn.Linear(n_features, 64), nn.ReLU(), nn.Linear(64, 1))
```

`Model(ABC)` is `fit(x_train, y_train, x_val, y_val)` / `predict(x)` /
`save(path)` / `load(path)` — the whole contract every architecture must
implement, `Registry(Model, ...)`-backed exactly like `Factor`/`Strategy`
elsewhere in this codebase. `SkorchModel` implements all four on top of
`skorch.NeuralNetRegressor`:

- Standardization: `sklearn.preprocessing.StandardScaler`, fit on **train
  only** — val/test never influence normalization.
- Early stopping: `skorch.callbacks.EarlyStopping(load_best=True)`.
- The caller's own pre-split, time-ordered validation set is wired in via
  `skorch.helper.predefined_split` — **not** skorch's default internal
  `train_split` (which re-splits `x_train` itself, randomly, and would
  silently reintroduce the exact leakage `time_split()` exists to prevent).
- Checkpointing: `save()`/`load()` write/restore skorch's own
  `save_params`/`load_params` state-dict file plus a small `<path>.meta.pkl`
  sidecar (the scaler, and every other instance attribute a concrete
  subclass stores on itself — e.g. `MLPModel`'s `hidden=` — generically,
  not a hand-picked subset, so `load()` always rebuilds the SAME shape a
  model was trained with, not a constructor's defaults). Same "a path, not
  a pickled blob" convention `tam/strategy/mlx_lora_client.py` uses for its
  LoRA adapter.

`@Registry.register(Model, "mlp")` (`MLPModel`) is the built-in default — a
small 2-3 layer MLP, `hidden=` the only architecture knob.

**Embeddings** (e.g. a per-ticker or per-sector learned representation) are
a `Model`-level concern, not a `FeatureStore` one — `nn.Embedding(num_tickers,
dim)` concatenated with the engineered features before the head is
something the network learns jointly with the rest of its weights, not
something precomputed/stored. A second `nn.Module`/`SkorchModel` subclass
accepting ticker ids alongside the feature tensor is the natural way to try
this — zero `FeatureStore`/harness changes needed.

## Analysis

```python
from tam.ml.analysis import feature_ic_summary, hit_rate, information_coefficient, quantile_spread

information_coefficient(test, "score", "fwd_return_3d")  # per-date Spearman rank correlation
quantile_spread(test, "score", "fwd_return_3d", n_quantiles=5)  # top-quantile minus bottom-quantile mean return, per date
hit_rate(test, "score", "fwd_return_3d")  # fraction of matching-sign rows
feature_ic_summary(test, ["ret_5d", "rsi_14", "score"], "fwd_return_3d")  # one row per column, ranked by mean IC
```

Pure functions over a `(date, ticker)`-indexed frame with a score column
and a realized-return label column — no model/strategy coupling, so they
work identically for a trained `Model`'s output and for a naive baseline
(any raw `Factor`'s own value) compared against it. `feature_ic_summary()`
runs the same three metrics over EVERY column given, independently — the
leaderboard answering "which of these already carries signal alone," not
just "does the model beat one chosen baseline."

## `run_experiment()` / `run_sweep()`

```python
from tam.ml.experiment import run_experiment, run_sweep

result = run_experiment(store, tickers, start, end, horizon=3, model="mlp", model_kwargs={"hidden": 32})
result.model_ic, result.model_spread, result.model_hit_rate  # vs. result.baseline_ic/baseline_spread/baseline_hit_rate
result.passed_gate  # model beats baseline on BOTH mean IC and mean quantile spread
result.feature_cols  # every registered feature's own column name in `result.test`
result.report()  # prints the full comparison + per-feature leaderboard; returns a 5-panel report

leaderboard = run_sweep(
    dict(store=store, start=start, end=end, model="mlp"),
    horizon=[1, 2, 3, 5],
    tickers=[tickers[:30], tickers[:100]],
)
```

`run_experiment()` is the one call replacing feature panel construction,
labeling, splitting, standardization, training, and baseline-comparison
analysis — `store.with_labels()` → `time_split()` → fit the named `Model`
→ score the test split → `ExperimentResult`. `baseline_col=` (default: the
store's first registered feature) picks which raw feature acts as the
naive baseline `passed_gate` compares against.

`run_sweep()` runs `run_experiment()` once per combination in the cartesian
product of `**grids` (via `sklearn.model_selection.ParameterGrid`) and
returns a leaderboard sorted by mean IC — the actual point of pulling all
of the above out of ad hoc notebook cells: iterate on universe/horizon/
features/architecture by changing a grid, not rewriting a training loop.
`run_sweep()` doesn't resolve universes or feature sets itself — a
`tickers=[...]`/`store=[...]` grid axis is just a list of already-built
objects.

## Turning research into a real strategy

Once `run_experiment()`'s gate actually passes, save the winning model
(`result.model.save(path)`) and wire it into a live `Strategy` — see
`ML_STRATEGY_COLAB.md` (repo root) for a full worked example: a
`BasketMLHoldStrategy` that scores its universe each close via the same
`FeatureStore` used for research, sizes positions with
[`inverse_vol_weights`](basket.md), and holds each position for a fixed
multi-day horizon. That guide also covers running all of the above in
Google Colab against the self-service market-data lake (see
[Market data](marketdata.md)'s "Self-service `DataProvider`/`MinuteBarSource`" section).
