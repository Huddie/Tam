"""Importing this package registers every built-in strategy's (Strategy, name)
factory, and every built-in Signal's (Signal, id), with tam.registry.Registry —
a config-driven caller only ever needs `from tam.strategy.base import Strategy`
plus a strategy name string, never a direct import of the concrete class.
"""
from . import (  # noqa: F401
    basket_overnight,
    buy_and_hold,
    intraday_hold,
    llm_trading,
    ma_crossover,
    ml_walk_forward,
    moving_average,
    overnight_hold,
    signals,
    trend_rotation,
)
