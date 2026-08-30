# Strategy

*Full generated reference: [`tam.strategy`](api/tam.strategy.rst).*

Your trading logic. A `Strategy` reacts to clock events and submits orders
through a bound `TradeGateway`:

```python
class Strategy(ABC):
    def state_change(self, state: State) -> None: ...  # START / RUNNING / END
    def on_event(self, event: Event) -> None: ...

    self.subscribe_to(topic)  # e.g. OPEN_TOPIC, EOD_TOPIC (tam.events.clock)
    self.trade.stocks([Order(...)])  # submit orders via the bound TradeGateway
    self.annotate("note")  # marks a vertical line on the eventual chart
```

An `Order` is `Order(ticker, side, qty, portfolio, price_basis=PriceBasis.CLOSE)`;
`qty` is either a plain int (static shares) or `{"pct": 100}` /
`{"pct": 20, "basis": "portfolio_value"}` (percentage, resolved at fill time —
see `tam/portfolio/orders.py`'s `Qty`).

## Registering a strategy for config-driven use

Register a factory function under a name — the config-driven runner never
imports a strategy directly, only looks it up by this name:

```python
from tam.registry import Registry
from tam.strategy import Strategy


@Registry.register(Strategy, "buy_and_hold")
def build_buy_and_hold(repository, portfolio_id: str, params: dict, cash: float) -> Strategy:
    return BuyAndHoldStrategy(params["ticker"], params.get("qty", {"pct": 100}), portfolio_id)
```

```yaml
backtest:
  strategies:
    - strategy: buy_and_hold
      portfolio_id: main
      params: {ticker: AAPL}
```

## Built-in strategies

See `tam/strategy/*.py` for the exact params each one takes — copy
whichever is closest to what you need:

| Name | What it does |
|---|---|
| `buy_and_hold` | Buys once, holds |
| `moving_average` | Trades crosses of price vs. a single moving average |
| `ma_crossover` | Trades crosses between a fast and slow moving average |
| `trend_rotation` | Rotates between assets based on trend strength |
| `ml_walk_forward` | Online-learning ML model, retrained on a rolling walk-forward basis |
| `overnight_hold` | Buy at close, sell at next open |
| `intraday_hold` | Buy at open, sell at same close |
| `llm_trading` | Queries a local or remote LLM each simulated day; optional LoRA fine-tuning via `mlx-lm` |
| `basket_overnight` | Cross-sectional, many-tickers-at-once — see [Basket research](basket.md) |

## Indicators

`tam.strategy.indicators` has the building blocks most strategies above are
built from — plain functions over a `pd.Series`, already named for
`timeseries()`/plotting (see [Charting](charting.md)):

```python
from tam.strategy.indicators import sma, rsi

sma_20 = sma(close, 20)  # .name == "sma_20"
rsi_14 = rsi(close, 14)  # .name == "rsi_14"
```
