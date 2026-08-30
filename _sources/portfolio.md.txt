# Portfolio & Trader

The book your strategy trades against.

```python
from tam.portfolio.portfolio import Portfolio
from tam.trading.trader import Trader

portfolio = Portfolio(portfolio_id, cash=10_000.0)  # tracks cash, positions, trade history
trader = Trader(name, strategy, portfolio)  # just pairs the two together
```

You rarely touch `TradeGateway` directly — it's what `self.trade.stocks(...)`
inside a `Strategy` actually calls; it resolves `Qty` specs into share
counts and mutates the right `Portfolio`.

## Transaction costs

```python
from tam.portfolio.portfolio import Portfolio
from tam.portfolio.costs import BpsCost

portfolio = Portfolio("main", cash=10_000.0, cost_model=BpsCost(rate=0.0005))  # 5bps per fill
```

Applied on every fill, both sides — a round trip (buy then sell, what an
overnight strategy does daily) costs `2 * rate` of notional. Defaults to
`ZeroCost` (today's behavior, unchanged) when omitted.

Config-driven:

```yaml
backtest:
  cost_model: {name: bps, rate: 0.0005}
```

A `Registry(CostModel, ...)` entry, same pattern as everything else
pluggable here — register your own for a more realistic model, e.g.
spread- or size-dependent.
