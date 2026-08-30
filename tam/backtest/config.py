"""Builds every strategy+portfolio in a backtest run from a config-driven list.

Each entry in `backtest.strategies` names a strategy registered under
(Strategy, name) in tam.registry.Registry, plus its own params — so which
strategies run, and what they're compared against, is a config change, not a
code change. New strategies just need one @Registry.register(Strategy, "name")
adapter function (see tam/strategy/buy_and_hold.py for an example).
"""

from __future__ import annotations

from ..data.repository import DataRepository
from ..portfolio.costs import CostModel
from ..portfolio.portfolio import Portfolio
from ..registry import Registry
from ..strategy.base import Strategy
from ..trading.trader import Trader


def build_strategies(
    repository: DataRepository, specs, default_cash: float, cost_model: CostModel | None = None
) -> tuple[list[Strategy], dict, list[Trader]]:
    strategies = []
    portfolios = {}
    traders = []
    for spec in specs:
        cash = float(spec.cash) if "cash" in spec else default_cash
        strategy = Registry.create(Strategy, spec.strategy, repository, spec.portfolio_id, spec.params, cash)
        portfolio = Portfolio(spec.portfolio_id, cash=cash, cost_model=cost_model)
        strategies.append(strategy)
        portfolios[spec.portfolio_id] = portfolio
        traders.append(Trader(spec.portfolio_id, strategy, portfolio))
    return strategies, portfolios, traders
