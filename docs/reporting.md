# Reporting

*Full generated reference: [`tam.backtest`](api/tam.backtest.rst) (`Report`,
`Tearsheet`, and the chart/metric registries all live under
`tam.backtest.*`).*

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
unlabeled sweep of hundreds of variants; plot those directly instead (see
[Charting](charting.md)).

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

## Tearsheet — a bigger, registry-driven multi-chart report

`tam.backtest.tearsheet` is an extensible, registry-driven report with the
same layout QuantStats' own tearsheet uses (charts stacked in one column,
metrics table alongside), rendered with our own Plotly figures:

```python
from tam.backtest.tearsheet import Tearsheet, WorstDrawdownPathsChart

ts = Tearsheet().add_chart("return_distribution_by_start_date").add_chart(WorstDrawdownPathsChart(threshold=-0.9))
ts.show(report)
ts.write(report, "tearsheet.html")
```

Two `Registry` interfaces, same pattern as elsewhere:

```python
@Registry.register(TearsheetChart, "my_chart")
class MyChart(TearsheetChart):
    title = "My Chart"

    def render(self, report: Report) -> go.Figure: ...


@Registry.register(TearsheetMetric, "my_metric")
class MyMetric(TearsheetMetric):
    label, format = "My Metric", "pct"

    def compute(self, report: Report, portfolio_id: str) -> float: ...
```

Reference either by its registered id in `charts=[...]`/`metrics=[...]`, or
pass an already-constructed instance directly (e.g. for non-default params:
`RollingSharpeChart(window_days=60)`).

A `TearsheetChart` is also directly callable and composable with `|`, the
same as everything in [Charting](charting.md) — see that page for the
`ChartCall`/`ChartPipeline` mechanics:

```python
from tam.backtest.tearsheet import CumulativeReturnsChart, DrawdownChart

c = CumulativeReturnsChart()
c(my_series)  # auto-displays in Jupyter
c1(series) | c2(series) | c3(series)  # one composite figure
```

Chart gallery (see `tam/backtest/tearsheet.py` for the full list and every
constructor's params): `CumulativeReturnsChart`, `LogCumulativeReturnsChart`,
`DrawdownChart`, `RollingSharpeChart`, `RollingSortinoChart`,
`RollingVolatilityChart`, `RollingReturnChart`, `RollingReturnHeatmapChart`,
`ReturnMatrixChart`, `MonthlyReturnsChart`, `MonthlyReturnsHeatmapChart`,
`MonthlyReturnsDistributionChart`, `EOYReturnsChart`,
`ReturnDistributionByStartDateChart`, `ReturnQuantilesChart`,
`WorstDrawdownPathsChart`, `WorstDrawdownPeriodsChart`,
`MaxDrawdownByStartDateChart`, `CagrByStartDateChart`, `SharpeByStartDateChart`,
`SharpeDifferenceByStartDateChart`, `MonthlyReturnByStartDateChart`,
`FinalValueByStartDateChart`. Metrics: `TotalReturnMetric`, `CagrMetric`,
`SharpeMetric`, `SortinoMetric`, `CalmarMetric`, `VolatilityMetric`,
`MaxDrawdownMetric`, `SkewMetric`, `KurtosisMetric`, `ValueAtRiskMetric`,
`ExpectedShortfallMetric`, `NumTradesMetric`.

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
