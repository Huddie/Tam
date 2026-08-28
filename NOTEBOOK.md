# Running tam-quant in Google Colab / Jupyter

`tam-quant` is the PyPI distribution for this repo's `tam` package (config-driven
backtesting for stocks/indices). `pip install`s as `tam-quant`; `import tam` in code
either way.

## Quick start (Colab)

Paste this into a fresh Colab cell:

```python
!pip install -q tam-quant
```

Then, in a new cell, write a config and run a backtest:

```python
import pathlib
from tam.backtest.runner import run_backtest

pathlib.Path("config.yaml").write_text("""
data:
  provider: yfinance
  store: parquet
  root: data/eod

backtest:
  tickers: [AAPL]
  start: "2022-01-01"
  end: "2024-01-01"
  cash: 10000
  report_path: output/report.html
  strategies:
    - strategy: moving_average
      portfolio_id: moving_average
      params:
        ticker: AAPL
        window: 5
        qty: 10
    - strategy: buy_and_hold
      portfolio_id: buy_and_hold
      params:
        ticker: AAPL
""")

report = run_backtest("config.yaml", live=False)
```

`run_backtest()` runs the full config-driven backtest, renders the equity/drawdown
chart directly in the cell's output (via Plotly's own rich-display protocol -- no
extra setup needed in Colab or Jupyter), and (with `live=False`, as above) returns
the `Report` object so you can keep working with it. `live` defaults to `True` --
see "Live-updating view" below -- which redraws the chart as the backtest runs and
returns `None` immediately instead; pass `live=False` whenever you want the
finished `Report` back right away, as in this quick-start example. Assign the
result to a variable, as above, rather than leaving the call as a cell's bare last
expression -- otherwise the notebook also auto-echoes
the `Report`'s `repr()` underneath the chart:

```python
report.summary_all()          # CAGR, Sharpe, max drawdown, etc. per portfolio
report.to_frame()              # daily snapshots as a DataFrame
report.trades_for("moving_average")   # that portfolio's trade log
```

This is the notebook-native counterpart to the CLI's `python -m examples.backtest
config.yaml` -- same config format, same strategies, same underlying engine. See the
main [README](README.md) and `examples/*_config.yaml` for the full set of built-in
strategies (`buy_and_hold`, `moving_average`, `ma_crossover`, `trend_rotation`,
`ml_walk_forward`, `overnight_hold`, `intraday_hold`, `llm_trading`) and every
config field they support.

## Live-updating view while a backtest runs

For a long backtest, `live=True` starts the run on a background thread and redraws
the same chart in place every couple of seconds as days complete -- the notebook
counterpart to `--mode live` on the CLI:

```python
run_backtest("config.yaml", live=True)
```

This returns `None` immediately (the chart keeps updating asynchronously in the
output area; there's no single finished `Report` yet at the moment the call
returns).

This does **not** use Dash, unlike `--mode live` on the CLI (which opens a real Dash
server for a real browser tab) -- Dash's own inline-in-notebook support depends on
correctly detecting a hosted notebook's reverse proxy, and Colab specifically is a
documented no-op case for that detection (confirmed empirically too: a Dash-backed
attempt here rendered a completely blank cell, no banner, no graph). It also doesn't
use IPython's `display(display_id=...)`/`update_display()` -- Colab's frontend
doesn't reliably replace rich HTML/JS content (e.g. a Plotly figure) in place via
that mechanism either (confirmed empirically: it kept stacking a new chart underneath
the old one on every refresh instead of replacing it). Instead, by default,
`live=True` clears the cell's output and redraws the chart from scratch on every
refresh, via `IPython.display.clear_output(wait=True)` -- the same trick every "live
matplotlib in Colab" tutorial uses for exactly this reason. No server, no iframe, no
separate URL, nothing that depends on how a given notebook host proxies ports.

`live=True` needs the `notebook` extra outside a real notebook kernel (a real
Jupyter/Colab kernel always has this already, since it's what powers the kernel
itself):

```python
!pip install -q "tam-quant[notebook]"
```

### Choosing a different live-rendering mode

`render_mode` picks which Presenter (see `tam/backtest/presenter.py`) drives that live
view -- by name, from the same `Registry` that strategies/data providers use elsewhere
in this project:

```python
run_backtest("config.yaml", live=True, render_mode="clear_output")   # default, described above
run_backtest("config.yaml", live=True, render_mode="native_dash")    # real Dash server, jupyter_mode="inline"
```

`"native_dash"` is the approach that doesn't work reliably in Colab (see above) --
kept available, not removed, for classic Jupyter/JupyterLab (where Dash's own docs
describe this as fully supported) or in case Colab's own support improves later.

`presenter_kwargs` passes through to whichever mode you picked, for anything it
accepts beyond the defaults -- e.g. a slower refresh interval, or falling back to a
clickable link instead of an iframe for `native_dash`:

```python
run_backtest("config.yaml", live=True, presenter_kwargs={"poll_seconds": 5.0})
run_backtest(
    "config.yaml", live=True, render_mode="native_dash", port=8060,
    presenter_kwargs={"jupyter_mode": "external"},
)
```

`show_trades_default=False` starts the equity chart's trade markers hidden instead
of shown (either way, a "Show/Hide Trades" button on the chart itself still lets a
viewer flip it afterward):

```python
run_backtest("config.yaml", show_trades_default=False)
```

See `tam/backtest/presenter.py`'s `NotebookPresenter` and `DashNotebookPresenter` for
everything each one accepts.

#### A fully custom presenter

`render_mode` only reaches classes already registered with
`@Registry.register(Presenter, "name")` -- ships with `"cli"`, `"clear_output"`, and
`"native_dash"` built in. Register your own the same way (any class implementing
`run_batch`/`show_report`/`run_live`, see `tam/backtest/presenter.py`'s `Presenter`
ABC) and reference it by name from either Python or config:

```python
from tam.backtest.presenter import Presenter
from tam.registry import Registry

@Registry.register(Presenter, "my_presenter")
class MyPresenter(Presenter):
    ...

run_backtest("config.yaml", live=True, render_mode="my_presenter")
```

Or skip the registry entirely and hand `run_backtest`/`run` an instance directly --
useful for a one-off presenter you don't want to register globally:

```python
run_backtest("config.yaml", presenter=MyPresenter())
```

### Controlling rendering from the config file itself

Everything above also has a config-file equivalent, via a top-level `report:`
section (a sibling of `data:`/`backtest:`) -- handy so a config checked into a repo
or shared with a teammate carries its own presentation choices, not just the
Python call site's:

```yaml
report:
  presenter: cli              # or "clear_output" / "native_dash" / a name you registered
  presenter_kwargs:
    poll_seconds: 2.5
  show_trades_default: false
  height: 900
```

An explicit Python argument (`render_mode=`, `presenter_kwargs=`, `show_trades_default=`,
or `presenter=` for a ready-made instance) always overrides whatever `report:` says;
omitting `report:` entirely (every config in `examples/` as of this writing) behaves
exactly as before this section existed.

## Running a backtest with a magic command

Inside a real notebook kernel, `%load_ext` registers `%backtest` as a line magic so
you don't have to write the `from tam.backtest.runner import run_backtest` import and
call yourself:

```python
%load_ext tam.notebook.magic

%backtest config.yaml
%backtest config.yaml --live
%backtest config.yaml --live --render-mode native_dash --poll-seconds 5 --show-trades false
```

Like any IPython line magic, capture its return value the normal way:

```python
report = %backtest config.yaml
```

See `tam/notebook/magic.py` for the full set of flags (mirrors `run_backtest`'s own
keyword arguments).

## Optional extras

Base install (`pip install tam-quant`) covers every built-in strategy except one:

| Extra | Adds | Needed for |
|---|---|---|
| `notebook` | `ipython` | `run_backtest(..., live=True)` outside a real notebook kernel |
| `live` | `dash` | `--mode live` on the CLI (a real Dash server, for a real browser tab) |
| `llm` | `mlx-lm` | `llm_trading`'s local self-fine-tuning LoRA client (`tam.strategy.mlx_lora_client`) |
| `quantstats` | `quantstats` (+ matplotlib/seaborn/tabulate) | `tam.backtest.quantstats_report` -- a much larger metrics/plot/tearsheet library, alongside `Report.summary()`/`visualization.render()`, not instead of them |

```python
!pip install -q "tam-quant[notebook]"   # only needed outside a real notebook kernel
!pip install -q "tam-quant[llm]"        # LoRA fine-tuning support
```

**`llm` will not install on Colab.** `mlx-lm` (and its dependency `mlx`) only run on
an M-series Mac (they use Metal) -- installing it will fail outright on Colab's Linux
runtime, not just leave an unusable feature installed. `llm_trading` itself still
works fine on Colab as long as you don't configure a `lora:` block in its config
(pointing `base_url`/`model` at a remote/HTTP-served LLM instead, e.g. an
OpenAI-compatible endpoint) -- only the local self-fine-tuning path needs `mlx-lm`.

## Querying the market-data lakes from Colab (minute bars + end-of-day)

`tam.marketdata` holds two Parquet lakes in the same Cloudflare R2 bucket, under
different prefixes: a 1-minute OHLCV lake (`minute/`) and tam.data's end-of-day
lake (`eod/`, the true daily bars fetched directly from a provider like yfinance --
NOT derived from the minute bars, so it carries a real dividend/split-adjusted
`adj_close` and generally covers a much longer history). Independent of the
`data:`/backtest config above. Install the extra first:

```python
!pip install -q "tam-quant[marketdata]"
```

**Recommended: a personal token, self-service, no admin involvement.** Create one
at `https://data.tamquant.com/settings/tokens` (requires GitHub login) -- it's
yours alone, and revoking it never affects anyone else's access. (This is the same
token used for publishing to Discovery below -- one token, not two; either site's
`/settings/tokens` page manages it.) Add it as a Colab secret named
`TAM_PAT` (key-icon panel, left sidebar), then:

```python
from tam.marketdata.explorer_client import fetch_dataframe, connect

df = fetch_dataframe("AAPL", 2024)      # one symbol-year as a DataFrame, plain HTTP

con = connect()                          # full SQL access over BOTH lakes
con.sql("SELECT * FROM daily_bars('AAPL') ORDER BY day").df()       # from minute bars
con.sql("SELECT * FROM eod_bars('AAPL') ORDER BY date").df()        # true EOD, adj_close included
con.sql("SELECT * FROM eod_bars('^GSPC') ORDER BY date").df()       # raw indices work too (Yahoo's "^" tickers)
con.sql("SELECT * FROM rolling_volatility('AAPL', 21) ORDER BY day").df()
```

`connect()` mints a short-lived, **read-only** R2 credential scoped to just this
bucket behind the scenes (Cloudflare's own R2 temporary-credentials scheme), refreshes
it automatically as it approaches expiry, and gives you the same macros as
`tam.marketdata.duckdb_query.open_duckdb()` below (`minute_bars`, `eod_bars`,
`daily_bars`, `weekly_bars`, `monthly_bars`, `rollup_bars`, `daily_returns`,
`rolling_volatility`) -- full glob/multi-file SQL, without ever handling real R2
account credentials yourself. See `https://data.tamquant.com/api-access` for
curl/plain-`requests` equivalents.

**Alternative, if you already have admin R2 credentials** (ingestion jobs, or
anyone who's been handed a real read-only R2 API token directly): add secrets
named exactly `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
`R2_BUCKET`, and:

```python
from tam.marketdata.duckdb_query import open_duckdb

con = open_duckdb(bucket="tam-data")
con.sql("SELECT * FROM daily_bars('AAPL') ORDER BY day").df()
con.sql("SELECT * FROM eod_bars('AAPL') ORDER BY date").df()
```

Functionally equivalent to `connect()` above -- this path exists mainly because
it's what ingestion jobs already use, and it doesn't depend on `data.tamquant.com`
being reachable. For an ordinary notebook, prefer the personal-token path.

## Third-party secrets, FRED data, and plotting raw time series

Anything that needs a third-party API key you bring yourself (not a `tam`-issued
credential like `TAM_PAT`/R2 access, which already have their own dedicated
resolvers) can go through `tam.Secrets`, which checks the same places Colab
notebooks and local scripts already expect:

```python
import tam

fred_key = tam.Secrets["FRED_API_KEY"]          # raises a clear error if not set anywhere
fred_key = tam.Secrets.get("FRED_API_KEY")      # None instead of raising, if you want to handle it yourself
```

Resolution order: an environment variable (directly, or via a local `.env` file) --
then, if running in Colab, a Colab secret of that same name (key-icon panel, left
sidebar). Nothing to configure differently between local and Colab; the same code
works in both.

### FRED (Treasury yields, Fed Funds rate, CPI, unemployment, ...)

```python
!pip install -q "tam-quant[fred]"
```

Add a Colab secret named exactly `FRED_API_KEY` (a free key from
[fred.stlouisfed.org](https://fred.stlouisfed.org) -- sign-up takes a couple
minutes, no approval wait), grant this notebook access to it, then:

```python
import tam

dgs10 = tam.Fred.get(tam.Fred.Datasets.TREASURY_10Y)   # or tam.Fred.get("DGS10") -- same series
dgs10.name    # "10-Year Treasury Yield", not the raw "DGS10" code
dgs10.tail()
```

`tam.Fred.Datasets` covers a handful of commonly-used series as a memory aid
(`TREASURY_3MO`/`TREASURY_2Y`/`TREASURY_10Y`/`TREASURY_30Y`, `FED_FUNDS_RATE`,
`FED_FUNDS_EFFECTIVE`, `SOFR`, `CPI`, `UNEMPLOYMENT_RATE`, `YIELD_CURVE_10Y_2Y`) --
FRED has tens of thousands of series total, so pass any other raw series id
(a plain string) straight to `.get()` just the same:

```python
tam.Fred.get("DGS2", start="2015-01-01", end="2024-01-01")   # start/end are optional; omit either for the full available history
```

The underlying `fredapi` client (and therefore the `FRED_API_KEY` lookup) is
built lazily on first `.get()` call -- `import tam` or referencing
`tam.Fred.Datasets` never requires a key to be configured, only actually
fetching a series does.

### SEC fundamentals (XBRL facts, financial statements, filings)

```python
!pip install -q "tam-quant[sec]"
```

`tam.research.data.sec` is its own small R2-backed data lake (same bucket,
under `sec/`) -- raw XBRL facts (full fidelity: taxonomy, unit, accession
number, filed date, ...) and a derived, normalized `financials` layer
(long format: one row per line item, e.g. `revenue`/`net_income`/
`total_assets`). Not exposed as `tam.Sec` at the top level like `tam.Fred`
-- import the class explicitly instead:

```python
from tam.research.data.sec import Sec

# No construction needed -- Sec.financials()/.filings()/.query() work
# directly on the class, via a shared default instance (reads from R2
# via the usual TAM_PAT token, same as connect() above):
Sec.financials(tickers=["AAPL", "MSFT"], statement="income_statement", start=2015)
Sec.filings(ticker="AAPL", forms=["10-K", "10-Q"], start="2015-01-01")
Sec.query("SELECT cik, fiscal_year, value FROM sec_stmt('income_statement') WHERE line_item = 'revenue'")

# Or construct your own instance for a different connection (raw R2
# credentials, a local Parquet tree, ...) -- completely separate from
# the shared default above, its own connection:
sec = Sec(local_root="data")
sec.financials(tickers=["AAPL"])
```

Every `Sec` method takes tickers OR raw CIKs interchangeably (`"AAPL"` or
`320193` both work) -- resolved via the same `sec/reference/
company_tickers.parquet` file EdgarTools' own ticker resolution is backed
by. Already wired into `connect()`/`open_duckdb()` above too, so the same
macros work directly in raw SQL over either connection:

```python
con.sql("SELECT * FROM sec_stmt('income_statement', 'AAPL') ORDER BY fiscal_year").df()
con.sql("SELECT * FROM sec_stmt('income_statement') WHERE line_item = 'revenue'").df()   # every company at once
con.sql("SELECT * FROM sec_facts('AAPL')").df()        # raw XBRL, full fidelity
con.sql("SELECT * FROM sec_filings('AAPL')").df()      # filing metadata: accession number, form, filed date, ...
con.sql("SELECT * FROM sec_companies() WHERE ticker = 'AAPL'").df()   # ticker/CIK/name reference table
```

`Sec.financials()`/`Sec.filings()` (the Python wrappers, not the raw SQL
macros above) do a couple of things for you that raw SQL doesn't:
`start_date`/`end_date`/`filed_date`/`period_of_report` come back as real
dates (cast in the query itself, not pandas afterward), rows are
pre-sorted, and -- the one genuinely non-obvious part -- a single filing
often reports BOTH a discrete-quarter figure and a year-to-date cumulative
one under the SAME `end_date` for the same `line_item` (SEC's own
`fiscal_year`/`fiscal_period` labels don't distinguish them). `financials()`
defaults to keeping only the shortest reported duration per
`(cik, line_item, end_date)` -- the discrete period -- via a window
function pushed into the query; pass `dedupe_periods=False` to get every
period SEC reported instead (e.g. if you specifically want the YTD
figures too).

Every input you have to pick a value for has a matching discovery method
that returns the real, legal options as a dataframe -- so you never have
to guess or go source-diving:

```python
Sec.companies(search="apple")                       # find a ticker/CIK: cik, ticker, entity_name
Sec.statements()                                     # valid statement= values
Sec.line_items(tickers=["AAPL"], search="rev")       # valid line_items= values for THIS company, ranked by fact_count
Sec.line_item_catalog(statement="balance_sheet")     # every line item we know how to normalize, whether or not AAPL reports it
Sec.concepts("revenue", tickers=["AAPL"])             # which raw XBRL tags rolled up into "revenue", per company
Sec.forms(tickers=["AAPL"])                          # valid forms= values for filings(), ranked by count
```

`line_items` accepts any canonical line-item name our normalization layer
knows -- `revenue`, `net_income`, `cost_of_revenue`, `gross_profit`,
`operating_income`, `ebitda`, `earnings_per_share_basic`/
`earnings_per_share_diluted`, `operating_cash_flow`/`investing_cash_flow`/
`financing_cash_flow`/`free_cash_flow`, `total_assets`/`total_liabilities`/
`stockholders_equity`, and more -- `Sec.line_items()`/`Sec.line_item_catalog()`
above are the current, authoritative list; a plot-ready quarterly trend is
then just:

```python
import pandas as pd
from tam.research.data.sec import Sec
from tam.charting import timeseries

financials = Sec.financials(tickers=["AAPL"], line_items=["revenue", "net_income", "operating_cash_flow"])

def series_for(line_item: str, label: str, n: int = 32) -> pd.Series:
    rows = financials[financials["line_item"] == line_item].sort_values("end_date")
    return rows.set_index("end_date")["value"].tail(n).rename(label)

timeseries([series_for("revenue", "Revenue"), series_for("net_income", "Net Income"),
            series_for("operating_cash_flow", "Operating Cash Flow")], title="AAPL fundamentals")
```

### Plotting raw time series (price + indicator overlays, FRED series, ...)

`tam.backtest.tearsheet`'s chart classes (`CumulativeReturnsChart`, `DrawdownChart`,
...) all normalize their input as an equity curve (% return, drawdown, ...) --
the right tool for backtest analytics, the wrong one for just plotting raw values
side by side. `timeseries()` (in `tam.charting`, since it's a general-purpose
plotting entry point, not backtest-specific) is the same callable/composable
chart API with no normalization applied:

```python
from tam.charting import timeseries
from tam.strategy.indicators import sma, rsi

close = con.sql("SELECT date, close FROM eod_bars('SPY') ORDER BY date").df().set_index("date")["close"]
sma_20, sma_50 = sma(close, 20), sma(close, 50)
rsi_14 = rsi(close, 14)

timeseries([close, sma_20, sma_50], title="SPY + SMA")   # auto-displays in a notebook cell (or call .show())
```

Accepts a single `pd.Series`, a plain `list` of them (each one's own `.name`
becomes its legend label -- `sma()`/`rsi()` already come back named `"sma_20"`/
`"rsi_14"`, so no manual renaming needed), a `{name: series}` dict, or a wide
`pd.DataFrame` (one column per name).

Chain multiple `timeseries()` calls with `|` for series on genuinely different
scales (RSI's 0-100 range doesn't belong on the same axis as price) -- each call
becomes its own subplot row in one composite figure, same as chaining any other
chart in this module:

```python
timeseries([close, sma_20, sma_50], title="Price") | timeseries(rsi_14, title="RSI")
```

FRED series plot the same way, no special-casing needed -- `tam.Fred.get(...)`
already returns a plain named `pd.Series`:

```python
timeseries([tam.Fred.get(tam.Fred.Datasets.TREASURY_2Y), tam.Fred.get(tam.Fred.Datasets.TREASURY_10Y)], title="Treasury Yields")
```

## Publishing dashboards to Discovery

Any Plotly figure -- or an already-rendered `.html` file -- can be published to
[Discovery](https://discovery.tamquant.com) (a private, GitHub-authenticated
catalog) directly from a notebook:

```python
from tam.discovery import upload

result = upload(fig, title="AAPL moving-average backtest", tags=["aapl", "moving-average"])
print(result.url)
```

This needs a publishing token (the API URL already defaults to
`https://discovery.tamquant.com`, nothing to configure there):
- Create a token once at `https://discovery.tamquant.com/settings/tokens`
  (requires GitHub login), then add it as a Colab secret named
  `TAM_PAT` -- or, if you're working locally instead of in Colab, run
  `upload-discovery login` once and it's saved for every future call.

`type=` groups this with other kinds of published HTML (default `"dashboard"`);
`name=` gives it a stable slug that always resolves to whichever version you
publish most recently, while the version's own URL (`result.url`) never changes
regardless of what you publish under that name later.

## Persisting data and reports across a Colab session

Colab's local filesystem is wiped when the runtime recycles. If you want ingested
price data or backtest artifacts to survive across sessions, mount Drive first and
point `data.root` / `backtest.report_path` at it:

```python
from google.colab import drive
drive.mount("/content/drive")
```

```yaml
data:
  provider: yfinance
  store: parquet
  root: /content/drive/MyDrive/tam-quant/data/eod

backtest:
  ...
  report_path: /content/drive/MyDrive/tam-quant/output/report.html
```

## Using a config file already in your Drive/repo

If you already have a `.yaml` config (e.g. one of this repo's `examples/*.yaml`,
or checked out via `!git clone`), just point `run_backtest` at its path directly --
no need to inline the YAML as a string:

```python
report = run_backtest("/content/drive/MyDrive/tam-quant/my_config.yaml")
```
