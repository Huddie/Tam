# LIB.md — tam's building blocks

`tam` is a set of small, independently-usable pieces that happen to compose into
a full config-driven backtester. Every piece below can be used on its own; the
config-driven runner (`tam.backtest.runner`) is just one way of wiring them
together. Most base types are pluggable via one shared mechanism:

```python
@Registry.register(BaseType, "name")
class MyImpl(BaseType): ...

instance = Registry.get(BaseType, "name")            # cached singleton, no-arg
instance = Registry.create(BaseType, "name", *args)  # fresh instance, args passed through
```
(`tam/registry.py`.) `DataProvider`, `DataStore`, `RepoWriter`, `FileFormat`,
`Strategy`, and `Presenter` all use this. Adding your own never requires
editing existing code.

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
from tam.data.providers import DataProvider
from tam.data.storage import DataStore
from tam.data.repository import DataRepository
from tam.data.writer import RepoWriter
from tam.registry import Registry

repo = DataRepository(Registry.get(DataProvider, "yfinance"), Registry.create(DataStore, "parquet", "data/eod"))
repo.ingest(["AAPL", "MSFT"], date(2020, 1, 1), date(2024, 1, 1))  # only fetches missing sub-ranges
df = repo.query("AAPL", date(2023, 1, 1), date(2023, 6, 1))         # cached in-memory after first read

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
from tam.data.export import export_history

export_history(
    "MU", date(2020, 1, 1), date(2024, 1, 1), "mu.csv",
    provider="yfinance",                                    # any registered DataProvider
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
    def state_change(self, state: State) -> None: ...   # START / RUNNING / END
    def on_event(self, event: Event) -> None: ...

    self.subscribe_to(topic)          # e.g. OPEN_TOPIC, EOD_TOPIC (tam.events.clock)
    self.trade.stocks([Order(...)])   # submit orders via the bound TradeGateway
    self.annotate("note")             # marks a vertical line on the eventual chart
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
`intraday_hold`, `llm_trading`) — copy whichever is closest to what you need.

## Portfolio & Trader — the book your strategy trades against

```python
portfolio = Portfolio(portfolio_id, cash=10_000.0)   # tracks cash, positions, trade history
trader = Trader(name, strategy, portfolio)           # just pairs the two together
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

run(config_path, mode="batch")                 # CLI-style: Rich progress bars, writes an HTML report
report = run_backtest(config_path, live=False) # notebook-style: returns the Report, renders inline
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
report.equity_curve("main")     # pd.Series, indexed by date
report.drawdown_curve("main")   # pd.Series
report.summary("main")          # dict: start/end value, CAGR, Sharpe, max drawdown, ...
report.summary_all()            # the above for every portfolio, as one DataFrame
report.trades_for("main")       # pd.DataFrame
```

Build one straight from your own pandas, no harness needed:
```python
from tam.backtest.report import Report

report = Report.from_curves({"my_strategy": wealth_series})       # {name: pd.Series} or a wide DataFrame
report = Report.from_curves(df, trades=trades_df, annotations=[{"date": d, "label": "note"}])
```
Scoped for a handful of named curves (a strategy comparison) — not an
unlabeled sweep of hundreds of variants; plot those directly instead.

## Rendering — turn a Report into a Plotly dashboard

```python
from tam.backtest.visualization import render, render_curves, write_html, RenderOptions

fig = render(report)                                  # equity/drawdown/trades/summary-table dashboard
fig = render_curves({"my_strategy": wealth_series})    # render(Report.from_curves(...)), one call
write_html(report, "out.html")                         # render(...).write_html(path)

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

report.summary_all()                                              # our 9 metrics
quantstats_report.metrics(report, "main", benchmark="alt")         # QuantStats' ~60, as a DataFrame

write_html(report, "dashboard.html")                               # our plotly dashboard
quantstats_report.write_html(report, "main", "tearsheet.html")    # a QuantStats tearsheet
```
`benchmark` can be another `portfolio_id` already in the *same* `Report`
(compares two strategies from one backtest run, no network) — or a raw
ticker string/`pd.Series`, which QuantStats resolves itself.

## Live updates — redraw as new data arrives

```python
from tam.backtest.live import live_render

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
    def run_live(self, harness, total_days, checkpoint_path, checkpoint_every, title, ticker_colors, prices, port, verbose) -> None: ...
```
Ships with `"cli"` (Rich progress + static HTML), `"clear_output"` (notebook,
default for `run_backtest`), `"native_dash"` (real Dash server inline).
Selectable by name from config (`report.presenter: cli`) or Python
(`run_backtest(..., render_mode="clear_output")`), or hand in your own
instance directly — no registration required:
```python
@Registry.register(Presenter, "my_presenter")   # optional -- only needed for name-based selection
class MyPresenter(Presenter): ...

run_backtest(config_path, presenter=MyPresenter())   # or render_mode="my_presenter"
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
