# Notebooks (Colab / Jupyter)

`tam-quant` is the PyPI distribution for this repo's `tam` package;
`pip install`s as `tam-quant`, `import tam` either way. Everything on this
page also works in a plain local Jupyter kernel, not just Colab.

## Quick start (Colab)

```bash
!pip install -q tam-quant
```

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
      params: {ticker: AAPL, window: 5, qty: 10}
    - strategy: buy_and_hold
      portfolio_id: buy_and_hold
      params: {ticker: AAPL}
""")

report = run_backtest("config.yaml", live=False)
```

`run_backtest()` renders the equity/drawdown chart directly in the cell's
output (Plotly's own rich-display protocol — no extra setup in Colab or
Jupyter) and, with `live=False` as above, returns the `Report` object.
`live` defaults to `True` (see below); pass `live=False` whenever you want
the finished `Report` back right away. Assign the result to a variable, as
above, rather than leaving the call as a cell's bare last expression —
otherwise the notebook also auto-echoes the `Report`'s `repr()` underneath
the chart.

```python
report.summary_all()                    # CAGR, Sharpe, max drawdown, etc. per portfolio
report.to_frame()                        # daily snapshots as a DataFrame
report.trades_for("moving_average")      # that portfolio's trade log
```

Same config format, same strategies, same underlying engine as the CLI's
`python -m examples.backtest config.yaml`. See [Strategy](strategy.md) for
every built-in and [Getting started](getting-started.md#config-shape) for
every config field.

## Live-updating view while a backtest runs

```python
run_backtest("config.yaml", live=True)
```

For a long backtest, starts the run on a background thread and redraws
the same chart in place every couple of seconds as days complete (the
notebook counterpart to `--mode live` on the CLI). Returns `None`
immediately — the chart keeps updating asynchronously.

This does **not** use Dash by default, unlike `--mode live` on the CLI —
Colab's own reverse-proxy detection is a documented no-op case for Dash's
inline support, and neither Dash nor `display(display_id=...)` reliably
replace rich HTML/JS content in place on Colab (confirmed empirically:
blank cells / stacked charts respectively). Instead `live=True` clears the
cell's output and redraws from scratch each refresh, via
`IPython.display.clear_output(wait=True)` — no server, no iframe, nothing
that depends on how a given notebook host proxies ports.

`live=True` needs the `notebook` extra outside a real notebook kernel (a
real Jupyter/Colab kernel already has this, since it's what powers the
kernel itself):

```bash
!pip install -q "tam-quant[notebook]"
```

### Choosing a different live-rendering mode

`render_mode` picks which [`Presenter`](backtest.md#presenter-how-a-report-live-loop-actually-gets-shown)
drives the live view, by name:

```python
run_backtest("config.yaml", live=True, render_mode="clear_output")   # default, described above
run_backtest("config.yaml", live=True, render_mode="native_dash")    # real Dash server, jupyter_mode="inline"
```

`"native_dash"` is the mode that doesn't work reliably in Colab (see
above) — kept available for classic Jupyter/JupyterLab, where Dash's own
docs describe this as fully supported.

`presenter_kwargs` passes through to whichever mode you picked:

```python
run_backtest("config.yaml", live=True, presenter_kwargs={"poll_seconds": 5.0})
run_backtest("config.yaml", live=True, render_mode="native_dash", port=8060, presenter_kwargs={"jupyter_mode": "external"})
```

`show_trades_default=False` starts the equity chart's trade markers hidden
(a "Show/Hide Trades" button still lets a viewer flip it afterward):

```python
run_backtest("config.yaml", show_trades_default=False)
```

### Controlling rendering from the config file itself

```yaml
report:
  presenter: cli              # or "clear_output" / "native_dash" / a name you registered
  presenter_kwargs:
    poll_seconds: 2.5
  show_trades_default: false
  height: 900
```

An explicit Python argument always overrides `report:`; omitting `report:`
entirely behaves exactly as before this section existed.

## Running a backtest with a magic command

```python
%load_ext tam.notebook.magic

%backtest config.yaml
%backtest config.yaml --live
%backtest config.yaml --live --render-mode native_dash --poll-seconds 5 --show-trades false
```

Capture its return value like any IPython line magic:

```python
report = %backtest config.yaml
```

See `tam/notebook/magic.py` for the full set of flags (mirrors
`run_backtest`'s own keyword arguments).

## Optional extras

Base install (`pip install tam-quant`) covers every built-in strategy
except one:

| Extra | Adds | Needed for |
|---|---|---|
| `notebook` | `ipython` | `run_backtest(..., live=True)` outside a real notebook kernel |
| `live` | `dash` | `--mode live` on the CLI (a real Dash server, for a real browser tab) |
| `llm` | `mlx-lm` | `llm_trading`'s local self-fine-tuning LoRA client |
| `quantstats` | `quantstats` + matplotlib/seaborn/tabulate | [QuantStats](reporting.md#quantstats-a-larger-alternative-stats-plots-engine) |
| `marketdata` | duckdb, pandas_market_calendars, boto3 | [Market data](marketdata.md) |
| `fred` | fredapi | [FRED](research-fred.md) |
| `sec` | edgartools | [SEC](research-sec.md) |

```bash
!pip install -q "tam-quant[notebook]"
!pip install -q "tam-quant[llm]"
```

**`llm` will not install on Colab** — `mlx-lm`/`mlx` only run on an
M-series Mac (Metal); Colab's Linux runtime can't install it at all.
`llm_trading` still works fine on Colab as long as you don't configure a
`lora:` block (point `base_url`/`model` at a remote/HTTP-served LLM
instead) — only the local self-fine-tuning path needs `mlx-lm`.

## Secrets in Colab

Anything needing a third-party API key (not a `tam`-issued credential like
`TAM_PAT`, which has its own resolver — see [Discovery](tam-discovery.md#authentication))
goes through `tam.Secrets`:

```python
import tam

fred_key = tam.Secrets["FRED_API_KEY"]      # raises a clear error if not set anywhere
fred_key = tam.Secrets.get("FRED_API_KEY")  # None instead of raising
```

Resolution order: an environment variable (directly, or via `.env`), then
— if running in Colab — a Colab secret of the same name (key-icon panel,
left sidebar). Same code works locally and in Colab.

## Querying the market-data lakes

See [Market data](marketdata.md) for the full architecture; from a
notebook, the self-service path needs no admin credentials at all:

```bash
!pip install -q "tam-quant[marketdata]"
```

Create a personal token at `https://data.tamquant.com/settings/tokens`
(GitHub login), add it as a Colab secret named `TAM_PAT` (same token
[Discovery](tam-discovery.md) publishing uses), then:

```python
from tam.marketdata.explorer_client import fetch_dataframe, connect

df = fetch_dataframe("AAPL", 2024)      # one symbol-year as a DataFrame, plain HTTP

con = connect()                          # full SQL access over minute bars + EOD + SEC lakes
con.sql("SELECT * FROM daily_bars('AAPL') ORDER BY day").df()
con.sql("SELECT * FROM eod_bars('AAPL') ORDER BY date").df()
```

`connect()` mints a short-lived, read-only R2 credential behind the
scenes and refreshes it automatically as it approaches expiry — see
[Data Explorer](tam-data-explorer.md) for the full client API.

## Persisting data and reports across a Colab session

Colab's local filesystem is wiped when the runtime recycles. Mount Drive
and point `data.root`/`backtest.report_path` at it if you need artifacts
to survive across sessions:

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
  report_path: /content/drive/MyDrive/tam-quant/output/report.html
```

Or skip Drive entirely and use R2 as the shared, persistent store instead
(reachable identically from Colab, a laptop, or CI) — see [Market data](marketdata.md).

## Using a config file already in your Drive/repo

Point `run_backtest` at an existing `.yaml`'s path directly — no need to
inline it as a string:

```python
report = run_backtest("/content/drive/MyDrive/tam-quant/my_config.yaml")
```
