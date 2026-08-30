# Charting

*Full generated reference: [`tam.charting`](api/tam.charting.rst).*

`tam.charting` is general-purpose, composable plotting: any `Chart` is
callable with data and returns a `ChartCall` that renders inline in a
Jupyter cell or via `.show()`. Chain multiple with `|` to combine them into
one composite figure. This is the plot-anything layer with no
return/drawdown normalization — for backtest-specific equity-curve charts,
see the [Tearsheet gallery](reporting.md#tearsheet-a-bigger-registry-driven-multi-chart-report),
which is built on the exact same `Chart`/`ChartCall`/`ChartPipeline` API.

## `timeseries()` — plot any series, raw

```python
from tam.charting import timeseries

timeseries(close)  # one line, uses close.name
timeseries([close, sma(close, 20), sma(close, 50)])  # several, each using its own .name
timeseries({"SPY": close, "SMA 20": sma_20})  # explicit names
```

Accepts a single `pd.Series`, a plain `list` of them (each one's own
`.name` becomes its legend label — `sma()`/`rsi()` already come back
named `"sma_20"`/`"rsi_14"`, so no manual renaming needed), a
`{name: series}` dict, or a wide `pd.DataFrame` (one column per name).

## `candles()` — OHLC, native candlesticks

```python
from tam import Symbol
from tam.charting import candles

aapl = Symbol("AAPL")
candles(aapl.eod_bars())  # "date"/open/high/low/close, auto-detected
candles(aapl.minute_bars())  # "ts" instead of "date" -- also auto-detected
candles(df, open="Open", high="High", low="Low", close="Close")  # explicit column names
```

A real `go.Candlestick` trace, not a `timeseries()` line — a candle needs
open/high/low/close per point, a shape `timeseries()`'s one-scalar-per-
curve model can't represent. `open`/`high`/`low`/`close` default to
lowercase column names, matching what `Symbol(...).eod_bars()`/
`.daily_bars()`/`.minute_bars()` already return with no renaming needed.
The x-axis is the DataFrame's own index if it has one, else a `date`/`ts`
column if present — pass `x=...` to override the guess, or `rangeslider=True`
to bring back Plotly's own default range slider (off by default here,
since it eats vertical space once piped into a multi-row `ChartPipeline`).
Composes with `|`/`&` exactly like every other chart:

```python
candles(aapl.eod_bars()) | timeseries(volume, title="Volume")
candles(aapl.eod_bars()) & rect(divergence_blocks, layer=-1)  # shaded behind the candles, same row
```

## Composite figures with `|`

Chain multiple calls for series on genuinely different scales (RSI's
0-100 range doesn't belong on the same axis as price) — each call becomes
its own subplot row in one composite figure, with its own y-axis:

```python
timeseries(close, title="Price") | timeseries(rsi_14, title="RSI")
```

A plain overlay within one `timeseries(...)` call shares a single axis
instead — use that when the series genuinely belong on the same scale
(e.g. price and its own moving averages).

FRED series plot the same way, no special-casing needed:

```python
import tam

timeseries(
    [tam.Fred.get(tam.Fred.Datasets.TREASURY_2Y), tam.Fred.get(tam.Fred.Datasets.TREASURY_10Y)], title="Treasury Yields"
)
```

## Shaded regions with `rect()`

Stack a thin panel of shaded vertical bands alongside `timeseries()` panels
via `|` — for marking date ranges (divergence episodes, regimes,
recessions, ...) on their own row, sharing the same x-axis as the panels
around it:

```python
from tam.charting import timeseries, rect

timeseries(spy) | rect(divergence_blocks, title="Divergence") | timeseries(yield_curve)
```

`regions` is a list of `(start, end)` tuples — anything Plotly accepts as
an x-axis value (dates, timestamps, numbers). This renders as its own row,
not a shaded overlay behind an adjacent panel's lines — for shading
directly behind a specific chart's own traces in the same panel, call
`fig.add_vrect(...)` on that chart's own rendered figure instead.

## Writing your own chart

```python
from tam.charting import Chart
import plotly.graph_objects as go


class MyChart(Chart):
    title = "My Chart"

    def render(self, data) -> go.Figure: ...  # `data` is whatever shape YOUR chart expects


MyChart()(my_data)  # a ChartCall -- auto-displays in Jupyter
MyChart()(my_data).show()  # explicit .show()
c1(data) | c2(data)  # a ChartPipeline -- one composite figure
```

`Chart` is deliberately not tied to `Report` or any other single shape —
`render(self, data)` takes whatever `data` that particular chart needs,
decided entirely by the subclass. `TimeSeriesChart` (what `timeseries()`
builds) accepts the same four series shapes `timeseries()` itself does;
`RectChart` (what `rect()` builds) takes a plain list of `(start, end)`
tuples instead, since it has no curves at all. Register a chart under a
name (`@Registry.register(Chart, "my_chart")`) to reference it by string
elsewhere, e.g. from `Tearsheet(charts=[...])`.

## Publishing a chart directly

Any `ChartCall`/`ChartPipeline` — or a plain Plotly `Figure` — can go
straight into [`tam.discovery.upload()`](tam-discovery.md) with no
intermediate `.html` file:

```python
from tam.discovery import upload

upload(timeseries(close, title="Price") | timeseries(rsi_14, title="RSI"), title="AAPL + RSI")
```
