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
