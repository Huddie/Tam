# Getting started

`tam` is config-driven event backtesting for stocks/indices: YAML in, an
interactive HTML dashboard out. Every piece (data provider, data store,
strategy, presenter) is independently usable — the config-driven runner is
just one way of wiring them together. See [Data](data.md), [Strategy](strategy.md),
[Backtest](backtest.md), etc. for each piece on its own.

## Install

```bash
pip install tam-quant
```

```python
import tam
```

Developing against this repo instead:

```bash
uv sync --extra dev
```

## Quickstart

```bash
uv run python -m examples.backtest examples/moving_average_config.yaml
```

Writes an interactive dashboard to `examples/output/moving_average_report.html`
and prints a summary table (returns, Sharpe, drawdown, ...).

## The registry pattern

Data providers, data stores, strategies, presenters, factors, cost models,
and universe providers are all pluggable through one shared mechanism:

```python
@Registry.register(BaseType, "name")
class MyImpl(BaseType): ...

instance = Registry.get(BaseType, "name")            # cached singleton, no-arg
instance = Registry.create(BaseType, "name", *args)  # fresh instance, args passed through
```

(`tam/registry.py`.) Adding your own implementation never requires editing
existing code — every config field below that takes a bare string name
(`provider: yfinance`, `strategy: buy_and_hold`, ...) is a registry lookup.

## Config shape

A config file has up to four top-level sections:

```yaml
data:                       # DataProvider/DataStore -- see Data
  provider: yfinance
  store: parquet
  root: data/eod
backtest:                   # the simulation itself -- see Backtest
  tickers: [AAPL]
  start: "2020-01-01"
  end: "2024-01-01"
  cash: 10000
  report_path: out.html
  strategies:
    - strategy: buy_and_hold   # a Registry(Strategy, ...) name
      portfolio_id: main
      params: {ticker: AAPL}
report:                     # OPTIONAL -- presenter/rendering knobs, see Backtest
  show_trades_default: false
export:                     # OPTIONAL -- only read by tam.data.export.run_export()
  symbol: AAPL
  start: "2020-01-01"
  end: "2024-01-01"
  path: aapl.csv
```

Every `strategy`, `data.provider`, and `data.store` name is a registry
lookup — the runner itself has zero strategy/provider-specific imports.

## Where to go next

| Component | Page |
|---|---|
| Fetch and cache OHLCV history | [Data](data.md) |
| Write trading logic | [Strategy](strategy.md) |
| The book a strategy trades against | [Portfolio & Trader](portfolio.md) |
| Run a simulation, config-driven or not | [Backtest](backtest.md) |
| Metrics and dashboards | [Reporting](reporting.md) |
| Plot any series, composite figures | [Charting](charting.md) |
| Screen a universe, build a basket | [Basket research](basket.md) |
| 1-minute OHLCV lake in R2 | [Market data](marketdata.md) |
| Macro series (rates, CPI, ...) | [FRED](research-fred.md) |
| XBRL facts and financial statements | [SEC](research-sec.md) |
| Publish dashboards to a catalog | [Discovery](tam-discovery.md) |
| Colab / Jupyter usage | [Notebooks](notebooks.md) |
