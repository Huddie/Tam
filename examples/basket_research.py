"""Research walkthrough: build a diversified overnight (BCSO) basket from a
universe of tickers, compare two candidate configs against each other, and
render the comparison -- entirely with tam.basket, no Strategy/Harness
involved. Copy this cell-by-cell into a notebook, or run it directly:

    uv run python examples/basket_research.py

Needs network access -- yfinance for prices. The S&P 500 list itself comes
from the `pitindex` package (bundled, offline point-in-time data, no network
for that part at all -- needs the `pitindex` extra, Python >=3.11); prices
are cached after the first run (to data/eod).
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from tam.backtest.quantstats_report import resolve_benchmark, returns_for
from tam.backtest.report import Report
from tam.backtest.visualization import render_curves
from tam.basket.factors import ExpectedShortfall, OvernightAlpha, Persistence, RollingSharpe, compute_factors, score
from tam.basket.matrix import price_matrix
from tam.basket.selection import cluster, select_diversified
from tam.basket.simulate import basket_wealth_curve
from tam.basket.universe import PitIndexUniverse, StaticUniverse
from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.schema import CLOSE, OPEN
from tam.data.storage import DataStore
from tam.registry import Registry

# --- 1. Universe & data ------------------------------------------------------
BENCHMARK = "SPY"

# The real, point-in-time S&P 500 via `pitindex` -- offline, no network for
# this part (see tam/basket/universe.py's PitIndexUniverse). Flip to False to
# skip straight to a small fixed list instead (e.g. the extra isn't
# installed, or you're iterating quickly).
USE_REAL_SP500 = True

if USE_REAL_SP500:
    universe = PitIndexUniverse(index="sp500")
    as_of_for_universe = date.today()  # constituents() is point-in-time -- pick any date you actually need
    # 500 names is a lot of yfinance calls for a demo -- slice down for speed.
    # Use universe.constituents(as_of_for_universe) directly (unsliced) for a
    # real run; a real backtest would also re-resolve this per rebalance date,
    # not once up front (tam.strategy.basket_overnight does exactly that).
    TICKERS = universe.constituents(as_of_for_universe)[:20]
else:
    # A small fixed list -- no network/pitindex dependency, no survivorship-
    # bias handling. Fine for quick iteration; swap back to PitIndexUniverse
    # above for anything you'd actually trust.
    TICKERS = ["AAPL", "MSFT", "NVDA", "AMD", "MU", "JPM", "BAC", "GS", "XOM", "CVX", "WMT", "COST", "PG"]
    universe = StaticUniverse(TICKERS)

repository = DataRepository(Registry.get(DataProvider, "yfinance"), Registry.create(DataStore, "parquet", "data/eod"))

end = date.today()
start = end - timedelta(days=365 * 4)
as_of = end - timedelta(days=180)  # leaves ~6 months after as_of to compare candidates out-of-sample

all_tickers = TICKERS + [BENCHMARK]
# price_matrix() is the actual primitive -- one raw column, aligned across
# tickers. The return DEFINITION (overnight, here) is just pandas on top of
# it, not a dedicated function: swap this one line for closes.pct_change()
# (close-to-close) or closes/opens - 1 (intraday) to research something else
# entirely, with everything below unchanged.
opens = price_matrix(repository, all_tickers, start, end, column=OPEN)
closes = price_matrix(repository, all_tickers, start, end, column=CLOSE)
returns = opens.shift(-1) / closes - 1  # buy at close, sell at next open (BCSO)
print(f"Return matrix: {returns.shape[0]} days x {returns.shape[1]} tickers, through {returns.index.max().date()}")

# price_matrix() silently drops a ticker whose query came back empty (no data
# for the requested range at all -- e.g. delisted, IPO'd after `start`, or a
# provider-specific symbol mismatch) -- so TICKERS itself may be a strict
# superset of returns.columns. Re-narrow here, once, rather than let a
# missing column surface later as a confusing KeyError.
TICKERS = [t for t in TICKERS if t in returns.columns]

# --- 2. Rolling, point-in-time-safe factors, as of `as_of` -------------------
# Only ever sees returns.loc[:as_of] -- see tam/basket/factors.py's _window().
factor_table = compute_factors(
    returns,
    as_of,
    {
        "sharpe_1y": RollingSharpe(window_days=252),
        "persistence": Persistence(period_days=60),
        "overnight_alpha": OvernightAlpha(window_days=252, benchmark=BENCHMARK),
        "expected_shortfall": ExpectedShortfall(window_days=252),
    },
).loc[TICKERS]  # drop the benchmark's own row -- it's not a candidate

print("\nFactor table as of", as_of)
print(factor_table.round(4))


def build_basket(weights_by_factor: dict, n_clusters: int, max_per_cluster: int, final_n: int, max_weight: float):
    """The full screener pipeline, parameterized -- change any of these
    inputs (factor weights, cluster count, cap, position size) to get a
    different candidate basket, without touching any other step."""
    candidate_scores = score(factor_table, weights_by_factor)
    candidates = candidate_scores[candidate_scores > 0].sort_values(ascending=False)
    if candidates.empty:
        return [], candidate_scores

    clusters = cluster(returns[candidates.index].loc[: pd.Timestamp(as_of)], n_clusters=min(n_clusters, len(candidates)))
    picks = select_diversified(candidates, clusters, n=final_n, max_per_cluster=max_per_cluster)

    volatility = returns[picks].loc[: pd.Timestamp(as_of)].tail(252).std()
    from tam.basket.weighting import inverse_vol_weights

    weights = inverse_vol_weights(candidate_scores[picks], volatility, max_weight=max_weight)
    return {t: w for t, w in weights.items() if w > 0}, candidate_scores


# --- 3. Two candidate configs, compared side by side -------------------------
diversified_weights, scores_a = build_basket(
    {"sharpe_1y": 0.4, "persistence": 0.3, "overnight_alpha": 0.2, "expected_shortfall": -0.1},
    n_clusters=5, max_per_cluster=1, final_n=5, max_weight=0.3,
)
concentrated_weights, scores_b = build_basket(
    {"sharpe_1y": 1.0},
    n_clusters=5, max_per_cluster=5, final_n=5, max_weight=0.3,
)

print("\n'diversified' picks:", {t: round(w, 3) for t, w in diversified_weights.items()})
print("'concentrated' picks:", {t: round(w, 3) for t, w in concentrated_weights.items()})

# --- 4. Simulate each config forward from as_of, and compare -----------------
forward_returns = returns.loc[pd.Timestamp(as_of) :]
wealth_diversified = basket_wealth_curve(forward_returns, diversified_weights)
wealth_concentrated = basket_wealth_curve(forward_returns, concentrated_weights)

report = Report.from_curves({"diversified": wealth_diversified, "concentrated": wealth_concentrated})
print("\n", report.summary_all()[["start_value", "end_value", "sharpe", "max_drawdown"]])

# Visual comparison -- .show() opens in a browser; swap for
# write_html(fig, "comparison.html") to save instead.
fig = render_curves({"diversified": wealth_diversified, "concentrated": wealth_concentrated}, title="Basket screener comparison")
fig.show()

# Optional: QuantStats' much larger metric set for the winner, benchmarked
# against the other candidate (needs the `quantstats` extra).
try:
    import quantstats as qs

    print("\nQuantStats metrics, diversified vs. concentrated:")
    print(qs.reports.metrics(returns_for(report, "diversified"), benchmark=resolve_benchmark(report, "concentrated"), display=False))
except ImportError as exc:
    print(f"\n(skipping QuantStats metrics: {exc})")
