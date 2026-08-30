"""A trading desk: pairs a Strategy's decisions with the Portfolio (book of cash,
positions, and trade history) it trades against."""

from __future__ import annotations

from ..portfolio.portfolio import Portfolio
from ..strategy.base import Strategy


class Trader:
    def __init__(self, name: str, strategy: Strategy, portfolio: Portfolio):
        self.name = name
        self.strategy = strategy
        self.portfolio = portfolio
