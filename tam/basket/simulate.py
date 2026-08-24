"""Simulate a static-weight basket's own return series directly from a return
matrix -- no Strategy/Portfolio/BacktestHarness involved. This is the tool
for RESEARCH iteration: try a factor weighting, a cluster count, a selection
size, get weights, see the resulting curve, compare against a different
config's curve -- all before ever committing to Strategy/Harness mechanics
(rebalance cadence, execution/fill details, costs). Once a config looks
right, tam.strategy.basket_overnight turns it into an actual tradeable
Strategy against the SAME building blocks.
"""
from __future__ import annotations

from typing import Dict, Union

import pandas as pd


def simulate_basket(returns: pd.DataFrame, weights: Union[pd.Series, Dict[str, float]]) -> pd.Series:
    """The basket's own daily return: the weighted sum of `returns`' columns
    named in `weights`. A ticker's missing/NaN day contributes 0 that day
    (not NaN) -- one ticker's data gap doesn't NaN out the whole basket."""
    weights = pd.Series(weights)
    aligned = returns.reindex(columns=weights.index).fillna(0.0)
    return aligned.mul(weights, axis=1).sum(axis=1)


def basket_wealth_curve(
    returns: pd.DataFrame, weights: Union[pd.Series, Dict[str, float]], starting_cash: float = 100_000.0
) -> pd.Series:
    """simulate_basket(...), compounded into a wealth curve -- feed straight
    into Report.from_curves({"config_a": ...})/render_curves({...}) to
    compare candidate screeners/weightings against each other visually, or
    into quantstats_report.metrics(Report.from_curves(...), "config_a") for
    a numeric comparison table. Static weights only (this simulates holding
    ONE selection/weighting for the whole period) -- a rebalancing basket
    that re-selects periodically is tam.strategy.basket_overnight's job, not
    this function's."""
    basket_returns = simulate_basket(returns, weights)
    return starting_cash * (1 + basket_returns.fillna(0.0)).cumprod()
