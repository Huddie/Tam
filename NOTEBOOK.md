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

report = run_backtest("config.yaml")
```

`run_backtest()` runs the full config-driven backtest, renders the equity/drawdown
chart directly in the cell's output (via Plotly's own rich-display protocol -- no
extra setup needed in Colab or Jupyter), and returns the `Report` object so you can
keep working with it. Assign the result to a variable, as above, rather than leaving
the call as a cell's bare last expression -- otherwise the notebook also auto-echoes
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
attempt here rendered a completely blank cell, no banner, no graph). Instead,
`live=True` redraws the chart via IPython's own `display()`/`update_display()` --
the same rich-display mechanism the non-live path's chart already uses successfully,
just refreshed periodically instead of drawn once. No server, no iframe, no separate
URL, nothing that depends on how a given notebook host proxies ports.

`live=True` needs the `notebook` extra outside a real notebook kernel (a real
Jupyter/Colab kernel always has this already, since it's what powers the kernel
itself):

```python
!pip install -q "tam-quant[notebook]"
```

## Optional extras

Base install (`pip install tam-quant`) covers every built-in strategy except one:

| Extra | Adds | Needed for |
|---|---|---|
| `notebook` | `ipython` | `run_backtest(..., live=True)` outside a real notebook kernel |
| `live` | `dash` | `--mode live` on the CLI (a real Dash server, for a real browser tab) |
| `llm` | `mlx-lm` | `llm_trading`'s local self-fine-tuning LoRA client (`tam.strategy.mlx_lora_client`) |

```python
!pip install -q "tam-quant[notebook]"   # only needed outside a real notebook kernel
!pip install -q "tam-quant[llm]"        # LoRA fine-tuning support
```

**`llm` will not install on Colab.** `mlx-lm` (and its dependency `mlx`) only run on
Apple Silicon (they use Metal) -- installing it will fail outright on Colab's Linux
runtime, not just leave an unusable feature installed. `llm_trading` itself still
works fine on Colab as long as you don't configure a `lora:` block in its config
(pointing `base_url`/`model` at a remote/HTTP-served LLM instead, e.g. an
OpenAI-compatible endpoint) -- only the local self-fine-tuning path needs `mlx-lm`.

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
