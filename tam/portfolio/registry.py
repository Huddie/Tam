"""Lookup table of portfolios by id, exposed to strategies as self.portfolios["id"]."""
from __future__ import annotations

from typing import Dict, Iterator, Tuple

from .portfolio import Portfolio


class PortfolioRegistry:
    def __init__(self, portfolios: Dict[str, Portfolio]):
        self._portfolios = dict(portfolios)

    def __getitem__(self, portfolio_id: str) -> Portfolio:
        return self._portfolios[portfolio_id]

    def __iter__(self) -> Iterator[Portfolio]:
        return iter(self._portfolios.values())

    def items(self) -> Iterator[Tuple[str, Portfolio]]:
        return iter(self._portfolios.items())
