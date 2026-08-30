# LIB.md — tam's building blocks

`tam` is a set of small, independently-usable pieces that happen to compose into
a full config-driven backtester. Every piece below can be used on its own; the
config-driven runner (`tam.backtest.runner`) is just one way of wiring them
together. Most base types are pluggable via one shared mechanism:

```python
@Registry.register(BaseType, "name")
class MyImpl(BaseType): ...


instance = Registry.get(BaseType, "name")  # cached singleton, no-arg
instance = Registry.create(BaseType, "name", *args)  # fresh instance, args passed through
```
(`tam/registry.py`.) `DataProvider`, `DataStore`, `RepoWriter`, `FileFormat`,
`Strategy`, `Presenter`, `UniverseProvider`, `Factor`, and `CostModel` all use
this. Adding your own never requires editing existing code.

---

## Data layer — fetch and cache OHLCV-or-whatever history

Three small interfaces, each independently pluggable:

```python
class DataProvider(ABC):
    def fetch_eod(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...


class DataStore(ABC):
    def exists(self, symbol: str) -> bool: ...
    def read(self, symbol: str) -> pd.DataFrame: ...
    def write(self, symbol: str, df: pd.DataFrame) -> None: ...
```

`DataRepository` composes a provider (fetch) and a store (cache), and is the
thing everything else actually calls:

```python
from datetime import date

from tam.data.providers import DataProvider
from tam.data.storage import DataStore
from tam.data.repository import DataRepository
from tam.data.writer import RepoWriter
from tam.registry import Registry

repo = DataRepository(Registry.get(DataProvider, "yfinance"), Registry.create(DataStore, "parquet", "data/eod"))
repo.ingest(["AAPL", "MSFT"], date(2020, 1, 1), date(2024, 1, 1))  # only fetches missing sub-ranges
df = repo.query("AAPL", date(2023, 1, 1), date(2023, 6, 1))  # cached in-memory after first read

# Hand the ingested data to a RepoWriter to persist it somewhere -- a
# Registry(RepoWriter, ...) entry, separate from DataStore's own cache
# format: some writers write flat files (the two built in do, one per
# symbol -- not DataStore's year-partitioned layout); nothing stops a
# custom one from shipping rows to S3/a database/returning an in-memory
# object instead. DataRepository.write() doesn't know or care which.
paths = repo.write(Registry.create(RepoWriter, "csv", "out/eod"), ["AAPL", "MSFT"])
# -> {"AAPL": Path("out/eod/AAPL.csv"), "MSFT": Path("out/eod/MSFT.csv")}
```

Ships with `"yfinance"`/`"fmp"` providers, `"csv"`/`"parquet"` stores
(year-partitioned on disk: `<root>/<SYMBOL>/<year>.<ext>`), and `"csv"`/
`"parquet"` `RepoWriter`s (flat `<root>/<SYMBOL>.<ext>`, one per symbol).
Add your own data source, cache format, or write destination with one
`@Registry.register(...)` class — nothing else in the codebase needs to change.

## Standalone export — fetch → transform (UDF) → flat file

No backtest involved at all — for when you just want one symbol's data in
your own hands, in one call:

```python
from datetime import date

from tam.data.export import export_history

export_history(
    "MU",
    date(2020, 1, 1),
    date(2024, 1, 1),
    "mu.csv",
    provider="yfinance",  # any registered DataProvider
    transform=lambda df: df.assign(ret=df["close"].pct_change()),  # any DataFrame -> DataFrame callable
)
```
This is the one-symbol, always-a-flat-file shortcut; reach for
`DataRepository.write(...)` above instead when you have several symbols
already ingested, or want a non-file `RepoWriter`.

Output format (`FileFormat`, shared with `RepoWriter` above) is also a
`Registry` entry (`"csv"`/`"parquet"` built in, inferred from `path`'s suffix
if `format=` is omitted) — register your own for feather/json/whatever.
Config-driven equivalent: `tam.data.export.run_export(config_path)`, reading a
`data:` + `export:` YAML section (see `examples/export_mu_config.yaml`);
`transform` stays Python-only since it's code, not YAML.

---

## Strategy — your trading logic

```python
class Strategy(ABC):
    def state_change(self, state: State) -> None: ...  # START / RUNNING / END
    def on_event(self, event: Event) -> None: ...

    self.subscribe_to(topic)  # e.g. OPEN_TOPIC, EOD_TOPIC (tam.events.clock)
    self.trade.stocks([Order(...)])  # submit orders via the bound TradeGateway
    self.annotate("note")  # marks a vertical line on the eventual chart
```

An `Order` is `Order(ticker, side, qty, portfolio, price_basis=PriceBasis.CLOSE)`;
`qty` is either a plain int (static shares) or `{"pct": 100}` /
`{"pct": 20, "basis": "portfolio_value"}` (percentage, resolved at fill time —
see `tam/portfolio/orders.py`'s `Qty`). Register a strategy for config-driven
use with a factory function:

```python
@Registry.register(Strategy, "buy_and_hold")
def build_buy_and_hold(repository, portfolio_id: str, params: dict, cash: float) -> Strategy:
    return BuyAndHoldStrategy(params["ticker"], params.get("qty", {"pct": 100}), portfolio_id)
```
See `tam/strategy/*.py` for the built-ins (`buy_and_hold`, `moving_average`,
`ma_crossover`, `trend_rotation`, `ml_walk_forward`, `overnight_hold`,
`intraday_hold`, `llm_trading`, `basket_overnight` -- a cross-sectional,
many-tickers-at-once strategy, see the `tam.basket` section below) — copy
whichever is closest to what you need.

## Portfolio & Trader — the book your strategy trades against

```python
from tam.portfolio.portfolio import Portfolio
from tam.trading.trader import Trader

portfolio = Portfolio(portfolio_id, cash=10_000.0)  # tracks cash, positions, trade history
trader = Trader(name, strategy, portfolio)  # just pairs the two together
```
You rarely touch `TradeGateway` directly — it's what `self.trade.stocks(...)`
inside a `Strategy` actually calls; it resolves `Qty` specs into share counts
and mutates the right `Portfolio`.

## BacktestHarness — running a simulation without any config at all

```python
from tam.backtest.harness import BacktestHarness

harness = BacktestHarness(repository, [strategy], {"main": portfolio}, dates, traders=[trader])
report = harness.run(checkpoint_path="run.pkl", checkpoint_every=1)  # both optional
```
Wires strategies/portfolios/an event clock together and drives one
`OPEN`+`EOD` tick per date. Returns a `Report`. This is what the config-driven
runner builds under the hood — construct it directly if you want full control
over strategies/portfolios without writing a YAML file.

---

## Config-driven runner — the batteries-included path

```python
from tam.backtest.runner import run, run_backtest

run(config_path, mode="batch")  # CLI-style: Rich progress bars, writes an HTML report
report = run_backtest(config_path, live=False)  # notebook-style: returns the Report, renders inline
```

A config file has up to four top-level sections:
```yaml
data:                       # DataProvider/DataStore -- see "Data layer" above
  provider: yfinance
  store: parquet
  root: data/eod
backtest:                   # the simulation itself
  tickers: [AAPL]
  start: "2020-01-01"
  end: "2024-01-01"
  cash: 10000
  report_path: out.html
  strategies:
    - strategy: buy_and_hold   # a Registry(Strategy, ...) name
      portfolio_id: main
      params: {ticker: AAPL}
report:                     # OPTIONAL -- presenter/rendering knobs, see below
  show_trades_default: false
export:                     # OPTIONAL -- only read by tam.data.export.run_export()
  symbol: AAPL
  start: "2020-01-01"
  end: "2024-01-01"
  path: aapl.csv
```
Every `strategy:` name and every `data.provider`/`data.store` name is a
`Registry` lookup — the runner has zero strategy/provider-specific imports.

---

## Report — the data object (no plotly dependency)

```python
report.equity_curve("main")  # pd.Series, indexed by date
report.drawdown_curve("main")  # pd.Series
report.summary("main")  # dict: start/end value, CAGR, Sharpe, max drawdown, ...
report.summary_all()  # the above for every portfolio, as one DataFrame
report.trades_for("main")  # pd.DataFrame
```

Build one straight from your own pandas, no harness needed:
```python
from tam.backtest.report import Report

report = Report.from_curves({"my_strategy": wealth_series})  # {name: pd.Series} or a wide DataFrame
report = Report.from_curves(df, trades=trades_df, annotations=[{"date": d, "label": "note"}])
```
Scoped for a handful of named curves (a strategy comparison) — not an
unlabeled sweep of hundreds of variants; plot those directly instead.

## Rendering — turn a Report into a Plotly dashboard

```python
from tam.backtest.visualization import render, render_curves, write_html, RenderOptions

fig = render(report)  # equity/drawdown/trades/summary-table dashboard
fig = render_curves({"my_strategy": wealth_series})  # render(Report.from_curves(...)), one call
write_html(report, "out.html")  # render(...).write_html(path)

options = RenderOptions(show_trades_default=False, height=900, template="plotly_dark")
fig = render(report, options=options)
```
No plotly import needed just to compute metrics (`report.py` stays
dependency-light) — only importing from `visualization.py` pulls it in.

## QuantStats — a larger, alternative stats/plots engine

Optional (`pip install "tam-quant[quantstats]"`) — feeds the same `Report`
into [QuantStats](https://github.com/ranaroussi/quantstats) for its ~60-metric
table, plot library, and HTML tearsheets. Alongside `Report.summary()`/
`render()`, not instead of them — call both on the same `Report`:

```python
from tam.backtest.visualization import write_html
from tam.backtest import quantstats_report

report.summary_all()  # our 9 metrics
quantstats_report.metrics(report, "main", benchmark="alt")  # QuantStats' ~60, as a DataFrame

write_html(report, "dashboard.html")  # our plotly dashboard
quantstats_report.write_html(report, "main", "tearsheet.html")  # a QuantStats tearsheet
```
`benchmark` can be another `portfolio_id` already in the *same* `Report`
(compares two strategies from one backtest run, no network) — or a raw
ticker string/`pd.Series`, which QuantStats resolves itself.

## `tam.basket` — cross-sectional (many-tickers-at-once) research

For screening a universe and building a diversified basket (e.g. "own stocks
with a persistent overnight edge, decorrelated from each other") -- a
research *toolkit*, not one fixed strategy: pull each piece you need, compare
candidate configs against each other, and only turn it into a real
`Strategy` once you know what you want. No `BacktestHarness` anywhere below.

```python
from datetime import date

from tam.basket.matrix import price_matrix
from tam.basket.factors import RollingSharpe, Persistence, OvernightAlpha, compute_factors, score
from tam.basket.selection import cluster, select_diversified
from tam.basket.weighting import inverse_vol_weights
from tam.basket.simulate import basket_wealth_curve
from tam.backtest.visualization import render_curves
from tam.data.schema import CLOSE, OPEN

# 1. price_matrix() is the actual primitive -- one raw OHLCV column, aligned
#    date x ticker. The return DEFINITION is plain pandas on top of it, not a
#    dedicated function per use case -- swap this one line to research
#    something else entirely (close-to-close, weekly, ...), everything below
#    stays the same:
opens = price_matrix(repository, tickers, date(2015, 1, 1), date(2024, 1, 1), column=OPEN)
closes = price_matrix(repository, tickers, date(2015, 1, 1), date(2024, 1, 1), column=CLOSE)
returns = opens.shift(-1) / closes - 1  # BCSO: buy close, sell next open
# returns = closes.pct_change()          # ...or the classic close-to-close daily return
# returns = closes / opens - 1           # ...or intraday (buy open, sell same close)

# 2. rolling, point-in-time-safe factors (only ever see data on/before as_of)
as_of = date(2023, 6, 1)
factors = compute_factors(
    returns,
    as_of,
    {
        "sharpe_3y": RollingSharpe(window_days=756),
        "persistence": Persistence(period_days=252),
        "alpha": OvernightAlpha(window_days=756, benchmark="SPY"),
    },
)
scores = score(factors, {"sharpe_3y": 0.5, "persistence": 0.3, "alpha": 0.2})

# 3. don't just take the top-N -- diversify across correlation clusters first
clusters = cluster(returns.loc[:as_of], n_clusters=8)
picks = select_diversified(scores, clusters, n=20, max_per_cluster=2)

# 4. weight inversely to volatility, capped
vol = returns[picks].loc[:as_of].tail(252).std()
weights = inverse_vol_weights(scores[picks], vol, max_weight=0.05)

# 5. compare THIS config's wealth curve against another candidate config, or
#    plot it on its own -- same render_curves()/Report already covered above
wealth = basket_wealth_curve(returns.loc[as_of:], weights)
render_curves({"this_config": wealth, "other_config": other_wealth}).show()
```
`price_matrix()` is deliberately the ONLY function in `tam.basket.matrix` --
the tedious part (per-symbol `DataRepository` storage -> one aligned
cross-sectional `DataFrame`) is the actual reusable primitive; which return
you compute from it is one line you write yourself, not something a named
function should decide for you or lock you into.

`RollingSharpe`/`Persistence`/`OvernightAlpha`/`OvernightBeta`/
`ExpectedShortfall`/`MaxDrawdown` are all `Registry(Factor, ...)` entries --
register your own the same way for anything not built in. Every one of them
is signed so higher raw value = better (`ExpectedShortfall`/`MaxDrawdown`
are negative-is-worse, so a LESS negative value is a HIGHER, still
"better," raw value) -- `score()` never re-signs a column, so a POSITIVE
weight always means "reward," a negative weight always means "reward the
opposite" of what you probably want, for every factor here alike.

`score()` itself is `Registry(ScoreFn, ...)`-backed (`method="zscore"`,
the default -- cross-sectional z-score each column, weighted sum; or
`method="rank"` -- centered percentile rank instead, more outlier-robust,
less sensitive to magnitude) -- register your own `ScoreFn` for a different
combination method and select it the same way, or via `basket_overnight`'s
`scoring: <id>` config field. Universe
membership (point-in-time, to avoid survivorship bias) is its own piece:
`tam.basket.universe.{StaticUniverse,CsvUniverse,WikipediaUniverse,PitIndexUniverse}`
(`Registry(UniverseProvider, ...)`) resolve `constituents(as_of)` from a fixed
list, a point-in-time membership file, a live Wikipedia fetch, or the
`pitindex` package, respectively -- one interface, swap providers via config
(`universe: {provider: pitindex, index: sp500}`) with no code change.

### Getting an S&P 500 ticker list

Two registered `UniverseProvider`s cover this without writing any fetch code
yourself:

```python
from datetime import date
from tam.basket.universe import PitIndexUniverse
from tam.registry import Registry
from tam.basket.universe import UniverseProvider  # just for the Registry.create call below

# pitindex: bundled, offline data -- no network at call time, needs the
# `pitindex` extra (Python >=3.11: `pip install "tam-quant[pitindex]"`).
# Covers "sp500" (default), "sp400", "sp600", or the composite "sp1500".
universe = PitIndexUniverse(index="sp500")
universe.constituents(date(2018, 6, 1))  # who was actually in the S&P 500 back then

# or resolve it by name, e.g. straight from config:
universe = Registry.create(UniverseProvider, "pitindex", index="sp600")
```
```python
from tam.basket.universe import fetch_sp500_from_wikipedia, fetch_sp500_membership, CsvUniverse, WikipediaUniverse

current_tickers, _changes = fetch_sp500_from_wikipedia()  # just today's list, no history

# WikipediaUniverse fetches once at construction (needs network then;
# constituents(as_of) itself doesn't hit the network again) -- or persist it
# to a file once and read it back with CsvUniverse:
fetch_sp500_membership().to_csv("sp500_membership.csv", index=False)  # {date,ticker,action}, ready for CsvUniverse
universe = CsvUniverse("sp500_membership.csv")
universe.constituents(date(2018, 6, 1))
```
`fetch_sp500_from_wikipedia()` scrapes Wikipedia's community-maintained "List
of S&P 500 companies" page (free, no API key, needs network) -- current
constituents plus a log of historical additions/removals.
`build_membership_events(current_members, changes)` (what
`fetch_sp500_membership()` calls internally) turns that log into the exact
`{date, ticker, action}` shape `CsvUniverse` reads -- and it's generic, not
Wikipedia- or S&P-500-specific, so it works for any index's change log with
the same `date`/`added_ticker`/`removed_ticker` shape.

Prefer `PitIndexUniverse` unless you specifically want Wikipedia's own page:
it's offline after install, covers sp400/sp600/sp1500 too, and doesn't depend
on a wiki page's table structure staying stable. For a backtest you'd trust
arbitrarily far back in time regardless of provider, a paid vendor's official
point-in-time feed (Sharadar, FMP's `historical-sp500-constituent`, ...) is
the most rigorous option -- register your own `UniverseProvider` against it
the same way.

Once a config like this looks right, `basket_overnight` (see the Strategy
section above, `examples/basket_overnight_config.yaml`) turns it into a real,
tradeable, config-driven `Strategy` against the exact same universe/factors/
selection/weighting building blocks -- monthly re-selection, daily
buy-close/sell-open execution, with optional vol targeting and a SPY-beta
hedge (short the benchmark sized to the basket's own weighted overnight
beta). That's the last step, once research on this page has told you what
you want -- everything above it stays useful on its own for getting there.

## Transaction costs

```python
from tam.portfolio.portfolio import Portfolio
from tam.portfolio.costs import BpsCost

portfolio = Portfolio("main", cash=10_000.0, cost_model=BpsCost(rate=0.0005))  # 5bps per fill
```
Applied on every fill, both sides -- a round trip (buy then sell, what an
overnight strategy does daily) costs `2 * rate` of notional. Defaults to
`ZeroCost` (today's behavior, unchanged) when omitted. Config-driven:
`backtest.cost_model: {name: bps, rate: 0.0005}` (a `Registry(CostModel, ...)`
entry, same pattern as everything else pluggable here — register your own
for a more realistic model, e.g. spread- or size-dependent).

## Walk-forward validation

```python
from datetime import date
from tam.backtest.walk_forward import run_walk_forward

report = run_walk_forward(
    "config.yaml",
    windows=[
        (
            date(2020, 1, 1),
            date(2020, 12, 31),
            date(2021, 1, 1),
            date(2021, 3, 31),
        ),  # (train_start, train_end, test_start, test_end)
        (date(2020, 4, 1), date(2021, 3, 31), date(2021, 4, 1), date(2021, 6, 30)),
    ],
)
report.summary_all()  # scored ONLY on each window's own test period, stitched together
```
Runs the same config once per window (over `[train_start, test_end]`, so the
strategy has real trailing history by `test_start` rather than starting
stone cold), keeps only each window's `[test_start, test_end]` slice, and
chains those slices' own returns (not absolute dollar levels -- each window
is a fresh harness with fresh starting cash) into one continuous
out-of-sample curve. The whole point: a strategy is never scored on a period
its own selection could have "seen" via the full history.

## Stress testing

```python
from tam.backtest.stress import stress_test, flat_shock

stress_test(weights, {"NVDA": -0.50})  # hypothetical portfolio return if NVDA gaps -50% overnight
stress_test(weights, flat_shock(weights, -0.05))  # every current position gaps -5%
```
Pure function, no `Report`/`Harness` needed -- run directly against
`BasketOvernightStrategy._target_weights` or any other `{ticker: weight}` you
already have, to see concentration risk directly (the same shock hurts more
against a 20%-weighted name than a 4%-weighted one).

## Live updates — redraw as new data arrives

```python
from tam.backtest.live import live_render
from tam.backtest.report import Report


def next_frame():
    return Report.from_curves({"my_strategy": running_series})  # or None: "nothing new yet"


live_render(next_frame, poll_seconds=2.0, should_continue=lambda: still_running)
```
Pull-based: `next_frame()` is polled every `poll_seconds` and redrawn via
`IPython.display.clear_output()`/`display()` until `should_continue()` is
False. No `BacktestHarness` required — drive it from your own loop (a
vectorized backtest extending a `Series` each tick, wrapped in
`Report.from_curves`). `live.serve(next_frame=...)` is the same idea for a
real Dash server/browser tab instead of a notebook cell.

## Presenter — how a Report/live-loop actually gets shown

```python
class Presenter(ABC):
    def run_batch(self, harness, total_days, checkpoint_path, checkpoint_every) -> Report: ...
    def show_report(self, report, title, ticker_colors, prices) -> None: ...
    def run_live(
        self, harness, total_days, checkpoint_path, checkpoint_every, title, ticker_colors, prices, port, verbose
    ) -> None: ...
```
Ships with `"cli"` (Rich progress + static HTML), `"clear_output"` (notebook,
default for `run_backtest`), `"native_dash"` (real Dash server inline).
Selectable by name from config (`report.presenter: cli`) or Python
(`run_backtest(..., render_mode="clear_output")`), or hand in your own
instance directly — no registration required:
```python
from tam.backtest.presenter import Presenter
from tam.backtest.runner import run_backtest
from tam.registry import Registry


@Registry.register(Presenter, "my_presenter")  # optional -- only needed for name-based selection
class MyPresenter(Presenter): ...


run_backtest(config_path, presenter=MyPresenter())  # or render_mode="my_presenter"
```

## Notebook magic

```python
%load_ext tam.notebook.magic
%backtest config.yaml --live --poll-seconds 5 --show-trades false
```
A thin `run_backtest(...)` wrapper — see `tam/notebook/magic.py` for every flag.

---

## Composing it all — no config file, no harness

Every piece above is independent, so you can skip the parts you don't need.
Example: fetch data yourself, run your own vectorized numpy backtest, and get
the same live-updating chart a full config-driven run would produce:

```python
from datetime import date

import pandas as pd

from tam.data.export import export_history
from tam.backtest.report import Report
from tam.backtest.live import live_render

export_history("MU", date(2020, 1, 1), date(2024, 1, 1), "mu.csv")

df = pd.read_csv("mu.csv", parse_dates=["date"]).set_index("date")
wealth = 100_000 * (1 + df["close"].pct_change().fillna(0)).cumprod()

live_render(lambda: Report.from_curves({"my_strategy": wealth}), should_continue=lambda: False)
```
No `Strategy`, `Portfolio`, `BacktestHarness`, or YAML config anywhere in that
chain — every layer above (`export_history`, `Report.from_curves`,
`live_render`) is a standalone function you can drop into any script.
