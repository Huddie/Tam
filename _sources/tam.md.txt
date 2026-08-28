# tam

Config-driven event backtesting: YAML in, an interactive HTML dashboard out.
Strategies, data providers, and stores are all pluggable via one shared
registry:

```python
@Registry.register(BaseType, "name")
class MyImpl(BaseType): ...

instance = Registry.get(BaseType, "name")            # cached singleton
instance = Registry.create(BaseType, "name", *args)  # fresh instance
```

Full API tour: [LIB.md](https://github.com/Huddie/Tam/blob/main/LIB.md).
Notebook/Colab usage: [NOTEBOOK.md](https://github.com/Huddie/Tam/blob/main/NOTEBOOK.md).

## Quickstart

```bash
uv sync --extra dev
uv run python -m examples.backtest examples/moving_average_config.yaml
```

Writes an interactive dashboard to `examples/output/moving_average_report.html`.

## Config shape

```yaml
data:
  provider: yfinance
  store: parquet
  root: data/eod
backtest:
  tickers: [AAPL]
  start: "2020-01-01"
  end: "2024-01-01"
  cash: 10000
  report_path: out.html
  strategies:
    - strategy: buy_and_hold
      portfolio_id: main
      params: {ticker: AAPL}
```

Every `strategy`/`provider`/`store` name is a registry lookup — the runner
has zero strategy-specific imports.

## Data layer

```python
from datetime import date
from tam.data.providers import DataProvider
from tam.data.storage import DataStore
from tam.data.repository import DataRepository
from tam.registry import Registry

repo = DataRepository(
    Registry.get(DataProvider, "yfinance"),
    Registry.create(DataStore, "parquet", "data/eod"),
)
repo.ingest(["AAPL", "MSFT"], date(2020, 1, 1), date(2024, 1, 1))
df = repo.query("AAPL", date(2023, 1, 1), date(2023, 6, 1))
```

Ships with `yfinance`/`fmp` providers and `csv`/`parquet` stores. Register
your own with a single `@Registry.register(...)` class.

## Strategy

```python
class Strategy(ABC):
    def state_change(self, state: State) -> None: ...   # START / RUNNING / END
    def on_event(self, event: Event) -> None: ...

    self.subscribe_to(topic)
    self.trade.stocks([Order(...)])
    self.annotate("note")
```

Built-ins: `buy_and_hold`, `moving_average`, `ma_crossover`, `trend_rotation`,
`ml_walk_forward`, `llm_trading`, `basket_overnight`. See `tam/strategy/*.py`.

## Report and rendering

```python
from tam.backtest.report import Report
from tam.backtest.visualization import render, write_html

report = Report.from_curves({"my_strategy": wealth_series})
report.summary_all()          # CAGR, Sharpe, max drawdown, ...
write_html(report, "out.html")
```

No plotly import needed just to compute metrics — only
`tam.backtest.visualization` pulls in the rendering dependency.

## Cross-sectional research (`tam.basket`)

```python
from datetime import date
from tam.basket.matrix import price_matrix
from tam.basket.factors import RollingSharpe, compute_factors, score
from tam.basket.selection import cluster, select_diversified
from tam.data.schema import CLOSE

closes = price_matrix(repo, tickers, date(2015, 1, 1), date(2024, 1, 1), column=CLOSE)
returns = closes.pct_change()

factors = compute_factors(returns, date(2023, 6, 1), {"sharpe_3y": RollingSharpe(756)})
scores = score(factors, {"sharpe_3y": 1.0})
picks = select_diversified(scores, cluster(returns, n_clusters=8), n=20)
```

A toolkit for screening a universe and building a diversified basket —
pull the pieces you need, no `Strategy`/harness required until you're ready
to trade it (`basket_overnight`).
