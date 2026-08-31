# ML basket-rotation strategy in Colab

A PyTorch (CPU-only) model that scores a basket of liquid S&P 500 names each
close, buys the ones it likes sized by its own confidence, and holds each
position for a fixed multi-day horizon before selling.

**The research harness (features, dataset/split, model, analysis, gate,
sweep) is real, tested `tam-quant` library code** — `tam.ml` — not
notebook-local classes anymore. Only the final live `Strategy` (step 6)
stays notebook-local, registered the same documented way `docs/strategy.md`
describes for "anything not built in." Paste each section into its own
Colab cell, in order. Everything here also works in a plain local Jupyter
kernel (see `NOTEBOOK.md`).

This is a *gated* pipeline: features → train → **look at the actual
signal-quality numbers** → only then wire up a live strategy and backtest.
Don't skip the gate in step 4.

## 0. Setup

`tam.ml` (and the `marketdata_eod` self-service `DataProvider`/`MinuteBarSource`
promoted alongside it) is new, unreleased code — install from a local wheel
or a git branch until a real `tam-quant` release ships it:

```python
# Option A: from a git branch (replace with your actual branch/remote)
!pip install -q "tam-quant[marketdata,ml,pitindex] @ git+https://github.com/Huddie/Tam.git@main"

# Option B: from a wheel you've already built locally (`uv build` in the repo)
# and uploaded to this Colab session
!pip install -q /content/tam_quant-*.whl[marketdata,ml,pitindex]
```

Once this lands in a real release, this becomes the ordinary
`!pip install -q "tam-quant[marketdata,ml,pitindex]"`.

Colab ships `torch` already (on both CPU and GPU runtimes) — `skorch`
(the `ml` extra's other dependency) is small and installs in seconds either
way. Everything below runs on CPU explicitly (`SkorchModel`'s own default),
since the whole point is a model cheap enough to retrain/run without a GPU.

`pitindex` (bundled, offline point-in-time S&P 500 membership) needs Python
≥3.11 — Colab's current default runtime satisfies this; check with
`!python --version` if unsure.

## 1. Universe & data

Uses this repo's own self-service market-data lake, not `yfinance` — the
SAME lake `tam-data-explorer` browses and `eod_bars()` queries, over the
lightweight, read-only `TAM_PAT` personal token (not admin R2 keys).
`tam.marketdata.eod_provider.MarketDataEodProvider` (registered as
`Registry(DataProvider, "marketdata_eod")` just by importing the module)
plugs straight into `DataRepository`/`price_matrix()` — and, unlike an
earlier draft of this guide, is now safe under concurrent fetches by
construction (`tam.marketdata.connection.thread_local_connection()` gives
each worker thread in `DataRepository.ingest()`'s thread pool its own
DuckDB connection, fixing a real crash confirmed earlier: sharing one
connection across threads crashed the whole kernel with no catchable
exception). No `max_workers=1` workaround needed anymore.

Create a personal token at `https://data.tamquant.com/settings/tokens`
(GitHub login) and add it as a Colab secret named `TAM_PAT` (key-icon
panel, left sidebar) — same token `docs/notebooks.md` already documents.

```python
from datetime import date, timedelta

import pandas as pd

from tam.basket.matrix import price_matrix
from tam.basket.universe import PitIndexUniverse
from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.schema import CLOSE
from tam.data.storage import DataStore
from tam.marketdata.eod_provider import MarketDataEodProvider  # noqa: F401 -- import registers "marketdata_eod"
from tam.registry import Registry

N_NAMES = 30
HORIZON = 3  # trading days -- the fixed hold, locked in for v1

universe = PitIndexUniverse(index="sp500")
TICKERS = universe.constituents(date.today())[:N_NAMES]

repository = DataRepository(
    Registry.get(DataProvider, "marketdata_eod"),
    Registry.create(DataStore, "parquet", "data/eod"),  # local cache -- ingest() only re-fetches actual gaps
)

end = date.today()
start = end - timedelta(days=365 * 6)  # ~6y: enough for train+val+test with room to spare

# One batched pre-fetch across every ticker, BEFORE price_matrix() -- price_matrix()
# ingests one ticker at a time in its own loop (tam/basket/matrix.py), so calling
# it cold would issue N sequential single-ticker DataRepository.ingest() calls
# instead of one batched, concurrent one.
repository.ingest(TICKERS, start, end)

closes = price_matrix(repository, TICKERS, start, end, column=CLOSE)
TICKERS = [t for t in TICKERS if t in closes.columns]  # price_matrix silently drops empty tickers
print(f"{len(TICKERS)} tickers, {closes.shape[0]} trading days, through {closes.index.max().date()}")
```

## 2. FeatureStore — register your factors

`tam.ml.feature_store.FeatureStore` wraps `tam.basket.factors.Factor` +
`Registry(Factor, ...)` (already point-in-time-safe by construction) with
registration + materialization + a Parquet cache — a real object, not a
bare `{name: Factor}` dict passed around by hand. The price-based factors
below (`RsiFactor`, `MacdFactor`, ...) are real, tested library code now
too — no need to define them locally the way an earlier draft of this
guide did.

```python
from tam.basket.factors import CrossSectionalRank, MacdFactor, RealizedVolFactor, RsiFactor, SmaDistanceFactor, TrailingReturnFactor
from tam.ml.feature_store import FeatureStore

store = FeatureStore(repository, cache_dir="feature_cache")
store.register_many(
    {
        "ret_1d": TrailingReturnFactor(1),
        "ret_5d": TrailingReturnFactor(5),
        "ret_10d": TrailingReturnFactor(10),
        "rsi_14": RsiFactor(14),
        "macd": MacdFactor(),
        "vol_10d": RealizedVolFactor(10),
        "sma_dist_20": SmaDistanceFactor(20),
        "rsi_14_xrank": CrossSectionalRank(RsiFactor(14)),
        "ret_5d_xrank": CrossSectionalRank(TrailingReturnFactor(5)),
    }
)
```

Want an intraday feature instead of (or alongside) these daily-price ones —
e.g. realized volatility in the last 30 minutes before close? See
`tam.basket.factors.IntradayVolatilityFactor` for a worked example built on
`tam.marketdata.minute_source.MinuteBarSource` (the same self-service,
thread-safe pattern as step 1's EOD provider, just for 1-minute bars) —
register it into `store` the same way as any other `Factor`.

## 3. Run the experiment

`run_experiment()` is the one call replacing what used to be five separate
notebook cells (feature panel + labels, time-ordered split, standardization,
an early-stopping training loop, and IC/quantile-spread/hit-rate analysis
vs. a naive baseline): `FeatureStore.with_labels()` → leakage-safe
`time_split()` → fit the named `Model` → score the test split → compare
against a baseline. "The meat" — which factors (step 2) and which
architecture (`model=`) — is everything you actually write.

```python
from tam.ml.experiment import run_experiment

result = run_experiment(
    store, TICKERS, start, end, horizon=HORIZON, model="mlp", model_kwargs={"hidden": 32}
)
result.report()  # prints mean IC/quantile-spread/hit-rate, model vs. baseline; returns a chart
```

Trying your own architecture needs exactly one new class — everything else
(standardization, the training loop, early stopping, checkpointing) is
handled by `tam.ml.model.SkorchModel`/`skorch` already:

```python
import torch.nn as nn

from tam.ml.model import Model, SkorchModel
from tam.registry import Registry


@Registry.register(Model, "my_mlp")
class MyMLP(SkorchModel):
    def _build_module(self, n_features):
        return nn.Sequential(nn.Linear(n_features, 64), nn.ReLU(), nn.Linear(64, 1))


result = run_experiment(store, TICKERS, start, end, horizon=HORIZON, model="my_mlp")
```

## 4. Decision gate — stop here if there's no real edge

**Only continue to step 6 if `result.passed_gate` is `True`** — the model
beating the naive baseline on both mean IC and mean quantile spread, the
exact rule this guide's first pass stated in prose, now a real, checkable
property instead of something to eyeball. A null result (mean IC near 0,
hit rate near a coin flip — see step 5 for how to explore *why*) means the
model isn't finding anything the crudest possible signal doesn't already
find; building a live strategy around it would just be trading noise with
extra steps.

## 5. Sweep — iterate on universe/horizon/features without rewriting anything

`run_sweep()` runs `run_experiment()` once per combination in a cartesian
product (via `sklearn.model_selection.ParameterGrid`) and returns a ranked
leaderboard — the actual point of pulling all of steps 2-4 out of ad hoc
notebook cells into `tam.ml` in the first place.

```python
from tam.ml.experiment import run_sweep

leaderboard = run_sweep(
    dict(store=store, start=start, end=end, model="mlp", model_kwargs={"hidden": 32}),
    horizon=[1, 2, 3, 5],
    tickers=[TICKERS[:30], universe.constituents(date.today())[:100]],
)
leaderboard  # ranked by mean_ic, includes passed_gate per row
```

Each `tickers=`/`store=` grid value is just something you already built —
`run_sweep()` doesn't resolve universes or feature sets itself. If a
sweep axis needs a different ticker LIST (not just a different count),
pre-ingest the union of every ticker you'll sweep over once, up front (same
`repository.ingest(...)` pattern as step 1), so no sweep point re-triggers
a fetch mid-run.

## 6. Live `Strategy` (notebook-local, registered like any other)

Only write this once step 4 passes. Subscribes to `EOD_TOPIC` only —
scoring and execution both happen at close, matching "buy at night." Tracks
open positions itself as a `{ticker: trading_days_remaining}` countdown
(decremented once per tick, sold when it hits zero) rather than target
calendar dates, so it never has to reason about weekends/holidays. **v1
simplification**: skips re-entering a ticker that's already held rather than
stacking overlapping tranches — a clear place to extend later, not a
silently-ignored gap.

First, save the winning experiment's model:

```python
CHECKPOINT_PATH = "model.pt"  # or a Drive path -- see step 7
result.model.save(CHECKPOINT_PATH)
```

Then the strategy itself — `Registry.create(Model, "mlp").load(...)`
reloads it, the same `Model.save`/`load` contract every architecture
implements, instead of hand-rolling `torch.load`/rebuilding an `nn.Module`
by hand the way an earlier draft of this guide did:

```python
from tam.basket.weighting import inverse_vol_weights
from tam.events.clock import EOD_TOPIC
from tam.events.types import State
from tam.ml.model import Model
from tam.portfolio.orders import Order, PriceBasis, Qty, QtyBasis, Side
from tam.strategy.base import Strategy


class BasketMLHoldStrategy(Strategy):
    def __init__(
        self,
        repository,
        tickers,
        checkpoint_path,
        feature_store,
        model_name,
        hold_days,
        portfolio_id,
        top_n=5,
        min_score=0.0,
        max_weight=0.2,
        vol_window=20,
        lookback_days=400,
    ):
        super().__init__()
        self._repository = repository
        self._tickers = tickers
        self._checkpoint_path = checkpoint_path
        self._feature_store = feature_store
        self._model_name = model_name
        self._hold_days = hold_days
        self._portfolio_id = portfolio_id
        self._top_n = top_n
        self._min_score = min_score
        self._max_weight = max_weight
        self._vol_window = vol_window
        self._lookback_days = lookback_days
        self._positions = {}  # ticker -> trading days remaining
        self._model = None  # lazy-loaded from checkpoint_path

    def state_change(self, state: State) -> None:
        if state is State.RUNNING:
            self.subscribe_to(EOD_TOPIC)

    def _ensure_model_loaded(self):
        if self._model is not None:
            return
        self._model = Registry.create(Model, self._model_name)
        self._model.load(self._checkpoint_path)

    def on_event(self, event) -> None:
        as_of = event.payload
        self._ensure_model_loaded()

        for ticker in list(self._positions):
            self._positions[ticker] -= 1
            if self._positions[ticker] <= 0:
                self.trade.stocks(
                    [Order(ticker=ticker, side=Side.SELL, qty=Qty(pct=100), portfolio=self._portfolio_id, price_basis=PriceBasis.CLOSE)]
                )
                del self._positions[ticker]

        window_start = as_of - timedelta(days=self._lookback_days)
        panel = self._feature_store.materialize(self._tickers, window_start, as_of)
        # .loc + boolean mask, not .xs() -- returns empty (not a KeyError) if as_of
        # isn't in the panel yet (e.g. insufficient warmup on the first few ticks).
        # The panel's date index is a Timestamp; as_of (the event payload) is a plain date.
        table = panel.loc[panel.index.get_level_values("date") == pd.Timestamp(as_of)]
        if table.empty:
            return

        scores = pd.Series(self._model.predict(table[self._feature_store.feature_names].to_numpy()), index=table.index.get_level_values("ticker"))

        candidates = scores[~scores.index.isin(self._positions)]
        candidates = candidates[candidates > self._min_score].sort_values(ascending=False).head(self._top_n)
        if candidates.empty:
            return

        closes_now = price_matrix(self._repository, self._tickers, window_start, as_of, column=CLOSE)
        volatility = closes_now[candidates.index].pct_change().tail(self._vol_window).std()
        weights = inverse_vol_weights(candidates, volatility, max_weight=self._max_weight)
        weights = weights[weights > 0]

        orders = [
            Order(ticker=ticker, side=Side.BUY, qty=Qty(pct=weight * 100, basis=QtyBasis.CASH), portfolio=self._portfolio_id, price_basis=PriceBasis.CLOSE)
            for ticker, weight in weights.items()
        ]
        if orders:
            self.trade.stocks(orders)
            for ticker in weights.index:
                self._positions[ticker] = self._hold_days

    def get_state(self) -> dict:
        return {"positions": dict(self._positions)}

    def load_state(self, state: dict) -> None:
        self._positions = state["positions"]
        self._model = None  # force a lazy reload from checkpoint_path on resume


@Registry.register(Strategy, "ml_basket_hold")
def build_ml_basket_hold(repository, portfolio_id, params, cash):
    return BasketMLHoldStrategy(
        repository,
        tickers=params["tickers"],
        checkpoint_path=params["checkpoint_path"],
        feature_store=store,  # closes over the notebook's own FeatureStore -- not YAML-serializable, so it's not read from params
        model_name=params.get("model_name", "mlp"),
        hold_days=params.get("hold_days", HORIZON),
        portfolio_id=portfolio_id,
        top_n=params.get("top_n", 5),
        min_score=params.get("min_score", 0.0),
        max_weight=params.get("max_weight", 0.2),
    )
```

`get_state()`/`load_state()` only ever carry the positions dict — never the
live model — matching `Strategy`'s own documented contract
(`tam/strategy/base.py`: "no live handles... reinjected via the
constructor"). Same reasoning `Model.save()`/`load()` already applies one
layer down (a checkpoint path, not a pickled blob).

## 7. Backtest

Same config-driven runner every other strategy in this library uses — see
`docs/backtest.md`. `data.provider: marketdata_eod` resolves via the SAME
`Registry` entry step 1's import already registered — the config-driven
runner looks providers/strategies up by name, no import of the concrete
class needed here. **Performance note**: `backtest.start` must cover the
full lookback the strategy needs (`start` minus `lookback_days`, i.e. the
same range already pulled in step 1) so the runner's own upfront ingestion
populates the `DataRepository`'s cache once — every per-tick call inside
`on_event` above then reads that cache instead of re-querying the lake.

```python
import pathlib

backtest_start = start  # from step 1 -- already includes the lookback the strategy needs

config_text = f"""
data:
  provider: marketdata_eod
  store: parquet
  root: data/eod
backtest:
  tickers: {TICKERS}
  start: "{backtest_start.isoformat()}"
  end: "{end.isoformat()}"
  cash: 100000
  report_path: output/ml_basket_hold_report.html
  strategies:
    - strategy: ml_basket_hold
      portfolio_id: ml_basket_hold
      params:
        tickers: {TICKERS}
        checkpoint_path: "{CHECKPOINT_PATH}"
        model_name: mlp
        hold_days: {HORIZON}
        top_n: 5
        max_weight: 0.2
"""
pathlib.Path("config.yaml").write_text(config_text)

from tam.backtest.runner import run_backtest

report = run_backtest("config.yaml", live=False)
report.summary_all()  # CAGR, Sharpe, max drawdown, etc.
```

For a rigorous out-of-sample view (the strategy never scored on a period
its own training could have "seen"), run the same config through
`tam.backtest.walk_forward.run_walk_forward` across a few rolling
`(train_start, train_end, test_start, test_end)` windows instead — see
`docs/backtest.md#walk-forward-validation`. Note that `run_walk_forward`
doesn't retrain the model per window itself; it re-runs the *live strategy*
(which loads the one already-trained checkpoint) over each window and keeps
only each window's test slice — re-run steps 2-3 with that window's
`train_end` as the cutoff yourself if you want the model itself, not just
the strategy's execution, walked forward.

## 8. Persisting across Colab session recycling

Colab's local filesystem is wiped when the runtime recycles. Point
`CHECKPOINT_PATH` (step 6) and `FeatureStore`'s `cache_dir` (step 2) at a
mounted Drive path if you want them to survive — see `docs/notebooks.md`'s
"Persisting data and reports across a Colab session" section (mount Drive,
point `data.root`/`backtest.report_path` at a Drive path in the config
above too) rather than repeating that guidance here.
