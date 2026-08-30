"""Point-in-time-safe rolling factors + cross-sectional scoring.

Every Factor only ever sees returns.loc[:as_of] (enforced by _window() below,
not by convention) -- the exact lookahead bug this whole module exists to
prevent structurally, not just document: a factor computed "as of" some date
must never see what happens after it, or a backtest built on it is measuring
something that couldn't have been known at the time.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd

from ..registry import Registry

_TRADING_DAYS_PER_YEAR = 252


def _window(returns: pd.DataFrame, as_of: date, window_days: int | None) -> pd.DataFrame:
    """Everything on or before `as_of`, then (if given) only the trailing
    `window_days` rows of THAT -- the one place point-in-time slicing happens,
    so every Factor below inherits it automatically rather than re-implementing
    (and risking getting wrong) the same `.loc[:as_of]` cutoff itself."""
    history = returns.loc[: pd.Timestamp(as_of)]
    return history.tail(window_days) if window_days is not None else history


class Factor(ABC):
    """One value per ticker (a column in `returns`), as of `as_of`."""

    @abstractmethod
    def compute(self, returns: pd.DataFrame, as_of: date) -> pd.Series: ...


@Registry.register(Factor, "sharpe")
class RollingSharpe(Factor):
    def __init__(self, window_days: int):
        self._window_days = window_days

    def compute(self, returns: pd.DataFrame, as_of: date) -> pd.Series:
        window = _window(returns, as_of, self._window_days)
        mean, std = window.mean(), window.std()
        sharpe = mean / std * (_TRADING_DAYS_PER_YEAR**0.5)
        return sharpe.where(std > 0, 0.0).fillna(0.0)


@Registry.register(Factor, "mean_return")
class MeanReturn(Factor):
    def __init__(self, window_days: int):
        self._window_days = window_days

    def compute(self, returns: pd.DataFrame, as_of: date) -> pd.Series:
        return _window(returns, as_of, self._window_days).mean().fillna(0.0)


@Registry.register(Factor, "persistence")
class Persistence(Factor):
    """Fraction of rolling `period_days`-long sub-windows (within the last
    `lookback_days`, default: everything available) with a POSITIVE mean
    return -- "how consistently has this edge worked," not just "is the
    long-run average good." A stock whose edge came from two lucky years
    scores far lower here than one that's worked in most rolling years, even
    if their long-run means are similar."""

    def __init__(self, period_days: int = _TRADING_DAYS_PER_YEAR, lookback_days: int | None = None):
        self._period_days = period_days
        self._lookback_days = lookback_days

    def compute(self, returns: pd.DataFrame, as_of: date) -> pd.Series:
        history = _window(returns, as_of, self._lookback_days)
        if len(history) < self._period_days:
            return pd.Series(0.0, index=returns.columns)
        rolling_mean = history.rolling(self._period_days).mean().dropna(how="all")
        return (rolling_mean > 0).mean().fillna(0.0)


@Registry.register(Factor, "expected_shortfall")
class ExpectedShortfall(Factor):
    """Mean return in the worst `1 - confidence` tail -- more negative is
    worse, on the SAME scale/direction as every other Factor here (higher
    raw value = better): a ticker with less-bad tail risk (e.g. -0.01) has a
    HIGHER raw value than one with worse tail risk (e.g. -0.05), so its
    cross-sectional z-score in score() is also higher, same as a better
    Sharpe/persistence/alpha ticker's z-score is higher. A POSITIVE score
    `weight` (see `score()`) is how a caller expresses "penalize tail risk"
    -- a negative weight does the opposite (rewards worse tail risk), since
    score() never re-signs a column based on which direction "good" happens
    to point in; it only z-scores the raw values as given."""

    def __init__(self, window_days: int, confidence: float = 0.99):
        self._window_days = window_days
        self._confidence = confidence

    def compute(self, returns: pd.DataFrame, as_of: date) -> pd.Series:
        window = _window(returns, as_of, self._window_days)

        def es(column: pd.Series) -> float:
            column = column.dropna()
            if column.empty:
                return 0.0
            threshold = column.quantile(1 - self._confidence)
            tail = column[column <= threshold]
            return float(tail.mean()) if len(tail) else float(threshold)

        return window.apply(es)


@Registry.register(Factor, "max_drawdown")
class MaxDrawdown(Factor):
    """Worst peak-to-trough decline over the window, always <= 0 -- same
    sign convention as ExpectedShortfall (higher/less-negative raw value =
    better): use a POSITIVE score() weight to penalize deep drawdowns."""

    def __init__(self, window_days: int):
        self._window_days = window_days

    def compute(self, returns: pd.DataFrame, as_of: date) -> pd.Series:
        window = _window(returns, as_of, self._window_days)

        def mdd(column: pd.Series) -> float:
            column = column.dropna()
            if column.empty:
                return 0.0
            wealth = (1 + column).cumprod()
            drawdown = wealth / wealth.cummax() - 1
            return float(drawdown.min())

        return window.apply(mdd)


def _regress(returns: pd.DataFrame, as_of: date, window_days: int, benchmark: str) -> pd.DataFrame:
    """Per-ticker OLS of returns[ticker] on returns[benchmark] over the
    trailing window -- {ticker: (alpha, beta)}. `benchmark` itself gets
    (alpha=0, beta=1) trivially (it's perfectly explained by itself)."""
    from sklearn.linear_model import LinearRegression

    window = _window(returns, as_of, window_days)
    bench = window[benchmark].dropna()
    rows = {}
    for ticker in window.columns:
        if ticker == benchmark:
            rows[ticker] = (0.0, 1.0)
            continue
        pair = pd.concat([window[ticker], bench], axis=1, keys=["y", "x"]).dropna()
        if len(pair) < 2:
            rows[ticker] = (0.0, 0.0)
            continue
        model = LinearRegression().fit(pair[["x"]], pair["y"])
        rows[ticker] = (float(model.intercept_), float(model.coef_[0]))
    return pd.DataFrame(rows, index=["alpha", "beta"]).T


@Registry.register(Factor, "overnight_alpha")
class OvernightAlpha(Factor):
    """The intercept of a regression of each ticker's returns on `benchmark`'s
    -- the part of a stock's own overnight return NOT explained by "the whole
    market moved overnight and this stock has beta to that." Rank on this,
    not raw mean return, to avoid mistaking market-wide overnight drift
    amplified by beta for a stock-specific edge."""

    def __init__(self, window_days: int, benchmark: str):
        self._window_days = window_days
        self._benchmark = benchmark

    def compute(self, returns: pd.DataFrame, as_of: date) -> pd.Series:
        return _regress(returns, as_of, self._window_days, self._benchmark)["alpha"]


@Registry.register(Factor, "overnight_beta")
class OvernightBeta(Factor):
    """The slope of the same regression as OvernightAlpha -- how much of this
    ticker's overnight move is just market beta. Used for hedge sizing
    (short the benchmark by portfolio_beta * hedge_fraction), not scoring."""

    def __init__(self, window_days: int, benchmark: str):
        self._window_days = window_days
        self._benchmark = benchmark

    def compute(self, returns: pd.DataFrame, as_of: date) -> pd.Series:
        return _regress(returns, as_of, self._window_days, self._benchmark)["beta"]


def compute_factors(returns: pd.DataFrame, as_of: date, factors: dict[str, Factor]) -> pd.DataFrame:
    """{factor_name: Factor} -> one date's factor table, index=ticker,
    columns=factor name."""
    return pd.DataFrame({name: factor.compute(returns, as_of) for name, factor in factors.items()})


class ScoreFn(ABC):
    """Turns a factor table (ticker x factor) + per-factor weights into one
    composite score per ticker -- the pluggable interface behind score()
    (Registry(ScoreFn, ...), the same "classic pattern" as Factor itself).
    Register your own for a different combination method and select it via
    basket_overnight's `scoring: <id>` config, or call
    Registry.get(ScoreFn, "<id>").compute(...) directly -- score() itself
    just calls Registry.get(ScoreFn, method) and stays the default entry
    point so no existing caller needs to change.

    Every weight's SIGN assumes "higher raw factor value = better" (see
    ExpectedShortfall/MaxDrawdown's own docstrings for why their signed,
    negative-is-worse values already satisfy this without re-signing) --
    that convention lives in the caller's weights, not in any ScoreFn
    implementation below."""

    @abstractmethod
    def compute(self, factor_table: pd.DataFrame, weights: dict[str, float]) -> pd.Series: ...


@Registry.register(ScoreFn, "zscore")
class ZScoreScoreFn(ScoreFn):
    """Cross-sectional z-score each named column, weighted sum -- e.g.
    weights={"sharpe_3y": 0.30, "overnight_alpha": 0.20, "expected_shortfall": 0.10}
    straight from config. A column with zero cross-sectional variance
    contributes 0 (not NaN/inf) for every ticker. The default -- today's
    exact (and only, before ScoreFn existed) behavior."""

    def compute(self, factor_table: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
        total = pd.Series(0.0, index=factor_table.index)
        for name, weight in weights.items():
            column = factor_table[name]
            std = column.std()
            z = (column - column.mean()) / std if std else pd.Series(0.0, index=column.index)
            total = total + weight * z.fillna(0.0)
        return total


@Registry.register(ScoreFn, "rank")
class RankScoreFn(ScoreFn):
    """Cross-sectional PERCENTILE RANK (centered to [-0.5, 0.5]) each named
    column instead of a z-score, weighted sum -- more robust to outliers
    (one extreme value can dominate a z-score via the mean/std it shifts,
    but can only ever occupy one rank position), at the cost of not
    distinguishing "slightly above average" from "way above average" the
    way a z-score does."""

    def compute(self, factor_table: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
        total = pd.Series(0.0, index=factor_table.index)
        for name, weight in weights.items():
            centered_rank = factor_table[name].rank(pct=True) - 0.5
            total = total + weight * centered_rank.fillna(0.0)
        return total


def score(factor_table: pd.DataFrame, weights: dict[str, float], method: str = "zscore") -> pd.Series:
    """Registry.get(ScoreFn, method).compute(factor_table, weights) --
    `method` defaults to "zscore" (today's exact behavior, unchanged), so
    every existing caller keeps working as before. Pass method="rank" (or
    your own registered ScoreFn id) to combine factors differently."""
    return Registry.get(ScoreFn, method).compute(factor_table, weights)
