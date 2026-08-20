"""Signal registry: named, described, self-contained technical indicators computed
from a price series. Registered via @Registry.register(Signal, "id") the same way
DataProvider/DataStore/Strategy implementations self-register (see registry.py) --
so any strategy can assemble its own feature set from plain config (a list of
{id, config} entries) instead of a fixed signal set hardcoded into that one
strategy, and adding a new signal never requires touching existing code.

Each Signal is a small, stateless, pure function of a close-price Series --
deliberately not decision/threshold logic (nothing here decides long vs. short,
buy vs. sell). A consumer -- e.g. an LLM prompt -- is handed the raw values plus
a plain-language `description` of what the number means, and is meant to learn
the relationship between signal values and outcomes itself rather than have that
relationship hardcoded for it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

import pandas as pd

from ..registry import Registry
from .indicators import bbands, macd_histogram, rsi, sma

ANNUALIZATION_FACTOR = 252 ** 0.5


class Signal(ABC):
    """compute() takes a close-price Series and returns a Series over the range
    where the signal is actually defined (NaN warmup rows dropped, and an empty
    Series -- not an error -- if there isn't even required_history() worth of
    data yet) -- callers that want a specific trailing window should slice the
    result themselves."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @abstractmethod
    def required_history(self) -> int:
        """Minimum number of trailing close prices needed to produce even one
        valid (non-NaN) value."""

    def compute(self, close: pd.Series) -> pd.Series:
        # Guarded once here rather than in every subclass: several underlying
        # tulipy calls raise (rather than returning empty/NaN) when handed
        # fewer rows than their period, so subclasses' _compute() must never
        # be called under-provisioned.
        if len(close) < self.required_history():
            return pd.Series(dtype=float)
        return self._compute(close)

    @abstractmethod
    def _compute(self, close: pd.Series) -> pd.Series: ...


def build_signals(specs) -> List[Signal]:
    """Turn a list of {id, config} or {id, configs} specs (e.g. straight from
    YAML) into Signal instances.

    - `config` (singular, optional): a single config dict -> one instance.
      Omitted means "use that signal's own constructor defaults".
    - `configs` (plural): a list of config dicts -> one instance per entry --
      sugar for repeating the same `id` with different config, e.g. five `sma`
      entries at different windows without writing out five separate specs.
    Exactly one of `config`/`configs` may be given per entry (or neither, for
    a single default instance)."""
    signals = []
    for spec in specs:
        signal_id = spec["id"]
        config = spec.get("config")
        configs = spec.get("configs")
        if config is not None and configs is not None:
            raise ValueError(f"signal {signal_id!r} spec has both 'config' and 'configs' -- use exactly one")
        config_dicts = configs if configs is not None else [config]
        signals.extend(Registry.create(Signal, signal_id, **dict(c or {})) for c in config_dicts)
    return signals


@Registry.register(Signal, "sma")
class PriceVsSma(Signal):
    """Trend: % distance of price from its own N-day simple moving average."""

    def __init__(self, window: int):
        self._window = window

    @property
    def name(self) -> str:
        return f"price_vs_sma_{self._window}"

    @property
    def description(self) -> str:
        return f"% distance from its own {self._window}-day SMA. + above (bullish), - below (bearish)."

    def required_history(self) -> int:
        return self._window

    def _compute(self, close: pd.Series) -> pd.Series:
        level = sma(close, self._window)
        return (close.reindex(level.index) / level - 1).dropna()


@Registry.register(Signal, "zscore")
class ZScoreVsSma(Signal):
    """Mean reversion: how many standard deviations price sits from its own
    N-day mean."""

    def __init__(self, window: int):
        self._window = window

    @property
    def name(self) -> str:
        return f"zscore_vs_sma_{self._window}"

    @property
    def description(self) -> str:
        return f"Std devs from its own {self._window}-day mean. Large + overbought, large - oversold."

    def required_history(self) -> int:
        return self._window

    def _compute(self, close: pd.Series) -> pd.Series:
        level = sma(close, self._window)
        rolling_std = close.rolling(self._window).std().reindex(level.index)
        return ((close.reindex(level.index) - level) / rolling_std).dropna()


@Registry.register(Signal, "rsi")
class RsiSignal(Signal):
    """Mean reversion / overbought-oversold oscillator, 0-100."""

    def __init__(self, period: int = 14):
        self._period = period

    @property
    def name(self) -> str:
        return f"rsi_{self._period}"

    @property
    def description(self) -> str:
        return f"RSI({self._period}), 0-100. >70 overbought, <30 oversold."

    def required_history(self) -> int:
        return self._period + 1

    def _compute(self, close: pd.Series) -> pd.Series:
        return rsi(close, self._period)


@Registry.register(Signal, "return")
class Return(Signal):
    """Momentum: trailing % return over a given horizon."""

    def __init__(self, horizon: int):
        self._horizon = horizon

    @property
    def name(self) -> str:
        return f"return_{self._horizon}d"

    @property
    def description(self) -> str:
        return f"% return over the last {self._horizon} days."

    def required_history(self) -> int:
        return self._horizon + 1

    def _compute(self, close: pd.Series) -> pd.Series:
        return close.pct_change(self._horizon).dropna()


@Registry.register(Signal, "volatility")
class RealizedVolatility(Signal):
    """Annualized realized volatility of daily returns over a trailing window."""

    def __init__(self, window: int = 20):
        self._window = window

    @property
    def name(self) -> str:
        return f"volatility_{self._window}d"

    @property
    def description(self) -> str:
        return f"Annualized realized vol, last {self._window} days. Higher = more turbulent."

    def required_history(self) -> int:
        return self._window + 1

    def _compute(self, close: pd.Series) -> pd.Series:
        return (close.pct_change().rolling(self._window).std() * ANNUALIZATION_FACTOR).dropna()


@Registry.register(Signal, "macd")
class Macd(Signal):
    """Trend/momentum: MACD histogram, normalized by price so it's comparable
    across price regimes."""

    def __init__(self, short_period: int = 12, long_period: int = 26, signal_period: int = 9):
        self._short_period = short_period
        self._long_period = long_period
        self._signal_period = signal_period

    @property
    def name(self) -> str:
        return f"macd_hist_{self._short_period}_{self._long_period}_{self._signal_period}"

    @property
    def description(self) -> str:
        return "MACD histogram / price. + bullish momentum, - bearish."

    def required_history(self) -> int:
        return self._long_period + self._signal_period

    def _compute(self, close: pd.Series) -> pd.Series:
        histogram = macd_histogram(close, self._short_period, self._long_period, self._signal_period)
        return (histogram / close.reindex(histogram.index)).dropna()


@Registry.register(Signal, "bollinger_pct_b")
class BollingerPercentB(Signal):
    """Mean reversion / volatility: where price sits within its own N-day
    Bollinger Bands."""

    def __init__(self, window: int = 20, stddev: float = 2.0):
        self._window = window
        self._stddev = stddev

    @property
    def name(self) -> str:
        return f"bollinger_pct_b_{self._window}"

    @property
    def description(self) -> str:
        return f"Position in {self._window}-day Bollinger Bands. ~1 upper band, ~0 lower band."

    def required_history(self) -> int:
        return self._window

    def _compute(self, close: pd.Series) -> pd.Series:
        lower, _, upper = bbands(close, self._window, self._stddev)
        pct_b = (close.reindex(lower.index) - lower) / (upper - lower)
        return pct_b.dropna()


@Registry.register(Signal, "distance_from_high")
class DistanceFromHigh(Signal):
    """Drawdown from price's own trailing N-day high."""

    def __init__(self, window: int = 252):
        self._window = window

    @property
    def name(self) -> str:
        return f"distance_from_high_{self._window}"

    @property
    def description(self) -> str:
        return f"% below its own {self._window}-day high. 0 = new high."

    def required_history(self) -> int:
        return self._window

    def _compute(self, close: pd.Series) -> pd.Series:
        rolling_high = close.rolling(self._window).max()
        return (close / rolling_high - 1).dropna()
