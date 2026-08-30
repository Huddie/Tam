# Basket research

For screening a universe and building a diversified basket (e.g. "own
stocks with a persistent overnight edge, decorrelated from each other") —
a research *toolkit*, not one fixed strategy: pull each piece you need,
compare candidate configs against each other, and only turn it into a
real `Strategy` once you know what you want. No `BacktestHarness` anywhere
below.

```python
from datetime import date
from tam.basket.matrix import price_matrix
from tam.basket.factors import RollingSharpe, Persistence, OvernightAlpha, compute_factors, score
from tam.basket.selection import cluster, select_diversified
from tam.basket.weighting import inverse_vol_weights
from tam.basket.simulate import basket_wealth_curve
from tam.charting import timeseries
from tam.data.schema import CLOSE, OPEN

# 1. price_matrix() is the actual primitive -- one raw OHLCV column, aligned
#    date x ticker. The return DEFINITION is plain pandas on top of it, not a
#    dedicated function per use case -- swap this one line to research
#    something else entirely (close-to-close, weekly, ...), everything below
#    stays the same:
opens = price_matrix(repository, tickers, date(2015, 1, 1), date(2024, 1, 1), column=OPEN)
closes = price_matrix(repository, tickers, date(2015, 1, 1), date(2024, 1, 1), column=CLOSE)
returns = opens.shift(-1) / closes - 1  # BCSO: buy close, sell next open
# returns = closes.pct_change()          # ...or the classic close-to-close daily return
# returns = closes / opens - 1           # ...or intraday (buy open, sell same close)

# 2. rolling, point-in-time-safe factors (only ever see data on/before as_of)
as_of = date(2023, 6, 1)
factors = compute_factors(
    returns,
    as_of,
    {
        "sharpe_3y": RollingSharpe(window_days=756),
        "persistence": Persistence(period_days=252),
        "alpha": OvernightAlpha(window_days=756, benchmark="SPY"),
    },
)
scores = score(factors, {"sharpe_3y": 0.5, "persistence": 0.3, "alpha": 0.2})

# 3. don't just take the top-N -- diversify across correlation clusters first
clusters = cluster(returns.loc[:as_of], n_clusters=8)
picks = select_diversified(scores, clusters, n=20, max_per_cluster=2)

# 4. weight inversely to volatility, capped
vol = returns[picks].loc[:as_of].tail(252).std()
weights = inverse_vol_weights(scores[picks], vol, max_weight=0.05)

# 5. compare THIS config's wealth curve against another candidate config, or
#    plot it on its own -- same timeseries()/Report from Charting/Reporting
wealth = basket_wealth_curve(returns.loc[as_of:], weights)
timeseries({"this_config": wealth, "other_config": other_wealth})
```

`price_matrix()` is deliberately the ONLY function in `tam.basket.matrix` —
the tedious part (per-symbol `DataRepository` storage -> one aligned
cross-sectional `DataFrame`) is the actual reusable primitive; which return
you compute from it is one line you write yourself, not something a named
function should decide for you or lock you into.

## Factors and scoring

`RollingSharpe`/`Persistence`/`OvernightAlpha`/`OvernightBeta`/
`ExpectedShortfall`/`MaxDrawdown` are all `Registry(Factor, ...)` entries —
register your own the same way for anything not built in. Every one of
them is signed so higher raw value = better (`ExpectedShortfall`/
`MaxDrawdown` are negative-is-worse, so a LESS negative value is a HIGHER,
still "better," raw value) — `score()` never re-signs a column, so a
POSITIVE weight always means "reward," a negative weight always means
"reward the opposite" of what you probably want, for every factor here alike.

`score()` itself is `Registry(ScoreFn, ...)`-backed (`method="zscore"`,
the default — cross-sectional z-score each column, weighted sum; or
`method="rank"` — centered percentile rank instead, more outlier-robust,
less sensitive to magnitude) — register your own `ScoreFn` for a different
combination method and select it the same way, or via `basket_overnight`'s
`scoring: <id>` config field.

## Universe membership

Point-in-time (to avoid survivorship bias) is its own piece:
`tam.basket.universe.{StaticUniverse,CsvUniverse,WikipediaUniverse,PitIndexUniverse}`
(`Registry(UniverseProvider, ...)`) resolve `constituents(as_of)` from a
fixed list, a point-in-time membership file, a live Wikipedia fetch, or the
`pitindex` package, respectively — one interface, swap providers via config
(`universe: {provider: pitindex, index: sp500}`) with no code change.

### Getting an S&P 500 ticker list

```python
from datetime import date
from tam.basket.universe import PitIndexUniverse
from tam.registry import Registry
from tam.basket.universe import UniverseProvider  # just for the Registry.create call below

# pitindex: bundled, offline data -- no network at call time, needs the
# `pitindex` extra (Python >=3.11: `pip install "tam-quant[pitindex]"`).
# Covers "sp500" (default), "sp400", "sp600", or the composite "sp1500".
universe = PitIndexUniverse(index="sp500")
universe.constituents(date(2018, 6, 1))  # who was actually in the S&P 500 back then

# or resolve it by name, e.g. straight from config:
universe = Registry.create(UniverseProvider, "pitindex", index="sp600")
```

```python
from tam.basket.universe import fetch_sp500_from_wikipedia, fetch_sp500_membership, CsvUniverse, WikipediaUniverse

current_tickers, _changes = fetch_sp500_from_wikipedia()  # just today's list, no history

# WikipediaUniverse fetches once at construction (needs network then;
# constituents(as_of) itself doesn't hit the network again) -- or persist it
# to a file once and read it back with CsvUniverse:
fetch_sp500_membership().to_csv("sp500_membership.csv", index=False)  # {date,ticker,action}, ready for CsvUniverse
universe = CsvUniverse("sp500_membership.csv")
universe.constituents(date(2018, 6, 1))
```

`fetch_sp500_from_wikipedia()` scrapes Wikipedia's community-maintained
"List of S&P 500 companies" page (free, no API key, needs network) —
current constituents plus a log of historical additions/removals.
`build_membership_events(current_members, changes)` (what
`fetch_sp500_membership()` calls internally) turns that log into the exact
`{date, ticker, action}` shape `CsvUniverse` reads — and it's generic, not
Wikipedia- or S&P-500-specific, so it works for any index's change log
with the same `date`/`added_ticker`/`removed_ticker` shape.

Prefer `PitIndexUniverse` unless you specifically want Wikipedia's own
page: it's offline after install, covers sp400/sp600/sp1500 too, and
doesn't depend on a wiki page's table structure staying stable. For a
backtest you'd trust arbitrarily far back in time regardless of provider,
a paid vendor's official point-in-time feed (Sharadar, FMP's
`historical-sp500-constituent`, ...) is the most rigorous option —
register your own `UniverseProvider` against it the same way.

## Turning research into a real strategy

Once a config like this looks right, `basket_overnight` (see
[Strategy](strategy.md), `examples/basket_overnight_config.yaml`) turns it
into a real, tradeable, config-driven `Strategy` against the exact same
universe/factors/selection/weighting building blocks — monthly
re-selection, daily buy-close/sell-open execution, with optional vol
targeting and a SPY-beta hedge (short the benchmark sized to the basket's
own weighted overnight beta). That's the last step, once research on this
page has told you what you want — everything above it stays useful on its
own for getting there.
