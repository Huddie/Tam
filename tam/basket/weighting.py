"""Inverse-volatility basket weighting with stock/sector caps -- research doc
§8's "don't start with a fancy optimizer" recommendation: a robust heuristic
(more capital to good-AND-stable-AND-low-risk names, capped so no single
name/sector dominates), not a constrained optimizer. True mean-variance
optimization (Ledoit-Wolf shrinkage covariance, a real QP solver) is a
natural later upgrade once this simpler version is validated -- see
research doc §9 -- not implemented here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def inverse_vol_weights(
    scores: pd.Series,
    volatility: pd.Series,
    max_weight: float = 1.0,
    sector_caps: dict[str, float] | None = None,
    sectors: pd.Series | None = None,
) -> pd.Series:
    """w_i ∝ max(score_i, 0) / vol_i, normalized to sum to 1, then capped at
    `max_weight` per name (excess redistributed proportionally among
    uncapped names) and, if `sector_caps`/`sectors` are given, capped per
    sector the same way. Negative scores get zero weight (long-only) --
    filter to your selected tickers (e.g. via select_diversified()) before
    calling this; it doesn't do selection itself, only weighting."""
    positive_score = scores.clip(lower=0.0)
    safe_vol = volatility.replace(0, np.nan).abs()
    raw = (positive_score / safe_vol).fillna(0.0)

    if raw.sum() <= 0:
        return pd.Series(0.0, index=scores.index)

    weights = raw / raw.sum()
    weights = _cap_iteratively(weights, max_weight)

    if sector_caps and sectors is not None:
        weights = _cap_sectors(weights, sectors, sector_caps)
        weights = _cap_iteratively(weights, max_weight)  # re-enforce the stock cap after sector redistribution

    return weights


def _cap_iteratively(weights: pd.Series, max_weight: float) -> pd.Series:
    """Cap every weight at `max_weight`, redistributing the excess
    proportionally among still-uncapped names, repeated until no weight
    exceeds the cap (a single pass can leave a name pushed back over the cap
    by redistribution -- bounded by len(weights) iterations, since each pass
    permanently caps at least one more name)."""
    weights = weights.copy()
    for _ in range(len(weights) + 1):
        over = weights > max_weight
        if not over.any():
            break
        excess = (weights[over] - max_weight).sum()
        weights[over] = max_weight
        under = ~over
        under_total = weights[under].sum()
        if under_total <= 0:
            break
        weights[under] = weights[under] + weights[under] / under_total * excess
    return weights


def _cap_sectors(weights: pd.Series, sectors: pd.Series, sector_caps: dict[str, float]) -> pd.Series:
    weights = weights.copy()
    for sector, cap in sector_caps.items():
        members = sectors[sectors == sector].index.intersection(weights.index)
        total = weights[members].sum()
        if total <= cap or total <= 0:
            continue
        scale = cap / total
        excess = total - cap
        weights[members] = weights[members] * scale
        others = weights.index.difference(members)
        other_total = weights[others].sum()
        if other_total > 0:
            weights[others] = weights[others] + weights[others] / other_total * excess
    return weights
