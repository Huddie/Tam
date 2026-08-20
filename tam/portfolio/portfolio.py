"""Tracks cash, open positions, and trade history for a single book."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List

from pydantic import BaseModel

from .orders import Order, Side


@dataclass
class Position:
    ticker: str
    qty: int = 0
    avg_price: float = 0.0


class Trade(BaseModel):
    date: date
    ticker: str
    side: Side
    qty: int
    price: float


class Portfolio:
    def __init__(self, portfolio_id: str, cash: float):
        self.id = portfolio_id
        self.cash = cash
        self._positions: Dict[str, Position] = {}
        self._trades: List[Trade] = []

    @property
    def tickers(self) -> List[str]:
        return list(self._positions.keys())

    @property
    def trades(self) -> List[Trade]:
        return list(self._trades)

    def position(self, ticker: str) -> Position:
        return self._positions.get(ticker, Position(ticker=ticker))

    def execute(self, order: Order, qty: int, price: float, as_of: date) -> None:
        """`qty` is the already-resolved share count -- Portfolio never looks at
        order.qty (which may be a percentage spec); see TradeGateway for resolution."""
        position = self._positions.setdefault(order.ticker, Position(ticker=order.ticker))
        notional = price * qty

        if order.side is Side.BUY:
            self.cash -= notional
            new_qty = position.qty + qty
            position.avg_price = (
                (position.avg_price * position.qty + notional) / new_qty if new_qty else 0.0
            )
            position.qty = new_qty
        else:
            self.cash += notional
            position.qty -= qty

        self._trades.append(
            Trade(date=as_of, ticker=order.ticker, side=order.side, qty=qty, price=price)
        )

    def market_value(self, prices: Dict[str, float]) -> float:
        holdings = sum(p.qty * prices.get(t, 0.0) for t, p in self._positions.items())
        return self.cash + holdings
