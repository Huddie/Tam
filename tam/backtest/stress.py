"""Explicit stress testing: what would THIS shock have done to a basket,
given its own weights -- not historical max drawdown, a hypothetical
scenario you name yourself (research doc: "SPY overnight -5%", "single
company -50%", ...). Pure function, no Report/Harness dependency -- usable
directly against tam.strategy.basket_overnight's own
BasketOvernightStrategy._target_weights, or any other {ticker: weight} you
already have.
"""
from __future__ import annotations

from typing import Dict, Union

import pandas as pd


def stress_test(weights: Union[pd.Series, Dict[str, float]], shocks: Union[pd.Series, Dict[str, float]]) -> float:
    """Hypothetical portfolio return under `shocks` (e.g. {"NVDA": -0.50} for
    "NVDA gaps down 50% overnight") -- sum(weight_i * shock_i) over every
    ticker in `weights`; a ticker with no shock given contributes 0 (assumed
    unaffected by this particular scenario, not "shocked by an unknown
    amount"). A 4%-per-name basket surviving one name's -50% shock (-2% total)
    is a very different risk profile than a 20%-per-name basket taking the
    same shock (-10%) -- this is how you'd see that difference directly."""
    weights = pd.Series(weights)
    shocks = pd.Series(shocks)
    return float(weights.mul(shocks.reindex(weights.index).fillna(0.0)).sum())


def flat_shock(weights: Union[pd.Series, Dict[str, float]], magnitude: float) -> Dict[str, float]:
    """The same shock applied to every currently-weighted ticker -- e.g.
    flat_shock(weights, -0.05) for "every position gaps down 5% overnight,"
    a convenient shorthand for stress_test's `shocks` argument when you don't
    need a per-ticker scenario."""
    return {ticker: magnitude for ticker in pd.Series(weights).index}
