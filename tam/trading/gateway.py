"""Executes order lists against portfolios at the harness's current simulation date."""
from __future__ import annotations

from datetime import date
from typing import Callable, List, Optional

from ..portfolio.orders import Order, PriceBasis, QtyBasis, Side
from ..portfolio.portfolio import Portfolio
from ..portfolio.registry import PortfolioRegistry

PriceLookup = Callable[[str, date, PriceBasis], float]


class TradeGateway:
    def __init__(self, portfolios: PortfolioRegistry, price_lookup: PriceLookup):
        self._portfolios = portfolios
        self._price_lookup = price_lookup
        self.current_date: Optional[date] = None

    def stocks(self, orders: List[Order]) -> None:
        if self.current_date is None:
            raise RuntimeError("TradeGateway has no active simulation date")
        for order in orders:
            portfolio = self._portfolios[order.portfolio]
            price = self._price_lookup(order.ticker, self.current_date, order.price_basis)
            qty = self._resolve_qty(order, portfolio, price)
            if qty > 0:
                portfolio.execute(order, qty, price, self.current_date)

    def _resolve_qty(self, order: Order, portfolio: Portfolio, price: float) -> int:
        spec = order.qty
        if spec.static is not None:
            return spec.static

        if order.side is Side.SELL:
            held = portfolio.position(order.ticker).qty
            return int(held * spec.pct / 100)

        if spec.basis is QtyBasis.PORTFOLIO_VALUE:
            prices = {
                ticker: self._price_lookup(ticker, self.current_date, PriceBasis.CLOSE)
                for ticker in portfolio.tickers
            }
            prices[order.ticker] = price
            budget_base = portfolio.market_value(prices)
        else:
            budget_base = portfolio.cash

        budget = budget_base * spec.pct / 100
        return int(budget // price)
