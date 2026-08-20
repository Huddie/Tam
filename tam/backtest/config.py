"""Builds every strategy+portfolio in a backtest run from a config-driven list.

Each entry in `backtest.strategies` names a strategy registered under
(Strategy, name) in tam.registry.Registry, plus its own params — so which
strategies run, and what they're compared against, is a config change, not a
code change. New strategies just need one @Registry.register(Strategy, "name")
adapter function (see tam/strategy/buy_and_hold.py for an example).
"""
from __future__ import annotations

from typing import List, Tuple

from ..data.repository import DataRepository
from ..portfolio.portfolio import Portfolio
from ..registry import Registry
from ..strategy.base import Strategy


def build_strategies(
    repository: DataRepository, specs, default_cash: float
) -> Tuple[List[Strategy], dict]:
    strategies = []
    portfolios = {}
    for spec in specs:
        cash = float(spec.cash) if "cash" in spec else default_cash
        strategy = Registry.create(Strategy, spec.strategy, repository, spec.portfolio_id, spec.params, cash)
        strategies.append(strategy)
        portfolios[spec.portfolio_id] = Portfolio(spec.portfolio_id, cash=cash)
    return strategies, portfolios
