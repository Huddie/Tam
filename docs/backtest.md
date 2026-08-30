# Backtest

*Full generated reference: [`tam.backtest`](api/tam.backtest.rst).*

## BacktestHarness — running a simulation without any config at all

```python
from tam.backtest.harness import BacktestHarness

harness = BacktestHarness(repository, [strategy], {"main": portfolio}, dates, traders=[trader])
report = harness.run(checkpoint_path="run.pkl", checkpoint_every=1)  # both optional
```

Wires strategies/portfolios/an event clock together and drives one
`OPEN`+`EOD` tick per date. Returns a [`Report`](reporting.md). This is what
the config-driven runner builds under the hood — construct it directly if
you want full control over strategies/portfolios without writing a YAML file.

## Config-driven runner — the batteries-included path

```python
from tam.backtest.runner import run, run_backtest

run(config_path, mode="batch")  # CLI-style: Rich progress bars, writes an HTML report
report = run_backtest(config_path, live=False)  # notebook-style: returns the Report, renders inline
```

See [Getting started](getting-started.md#config-shape) for the full config
shape. Every `strategy:` name and every `data.provider`/`data.store` name is
a `Registry` lookup — the runner has zero strategy/provider-specific imports.

## Presenter — how a Report/live-loop actually gets shown

```python
class Presenter(ABC):
    def run_batch(self, harness, total_days, checkpoint_path, checkpoint_every) -> Report: ...
    def show_report(self, report, title, ticker_colors, prices) -> None: ...
    def run_live(
        self, harness, total_days, checkpoint_path, checkpoint_every, title, ticker_colors, prices, port, verbose
    ) -> None: ...
```

Ships with `"cli"` (Rich progress + static HTML), `"clear_output"`
(notebook, default for `run_backtest` — see [Notebooks](notebooks.md)),
`"native_dash"` (real Dash server inline). Selectable by name from config
(`report.presenter: cli`) or Python (`run_backtest(..., render_mode="clear_output")`),
or hand in your own instance directly — no registration required:

```python
from tam.backtest.presenter import Presenter
from tam.backtest.runner import run_backtest
from tam.registry import Registry


@Registry.register(Presenter, "my_presenter")  # optional -- only needed for name-based selection
class MyPresenter(Presenter): ...


run_backtest(config_path, presenter=MyPresenter())  # or render_mode="my_presenter"
```

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

Runs the same config once per window (over `[train_start, test_end]`, so
the strategy has real trailing history by `test_start` rather than
starting stone cold), keeps only each window's `[test_start, test_end]`
slice, and chains those slices' own returns (not absolute dollar levels —
each window is a fresh harness with fresh starting cash) into one
continuous out-of-sample curve. The whole point: a strategy is never
scored on a period its own selection could have "seen" via the full history.

## Stress testing

```python
from tam.backtest.stress import stress_test, flat_shock

stress_test(weights, {"NVDA": -0.50})  # hypothetical portfolio return if NVDA gaps -50% overnight
stress_test(weights, flat_shock(weights, -0.05))  # every current position gaps -5%
```

Pure function, no `Report`/`Harness` needed — run directly against
`BasketOvernightStrategy._target_weights` or any other `{ticker: weight}`
you already have, to see concentration risk directly (the same shock hurts
more against a 20%-weighted name than a 4%-weighted one).

## Composing it all — no config file, no harness

Every piece here is independent, so you can skip the parts you don't need.
Example: fetch data yourself, run your own vectorized numpy backtest, and
get the same live-updating chart a full config-driven run would produce:

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

No `Strategy`, `Portfolio`, `BacktestHarness`, or YAML config anywhere in
that chain — every layer above (`export_history`, `Report.from_curves`,
`live_render`) is a standalone function you can drop into any script.
