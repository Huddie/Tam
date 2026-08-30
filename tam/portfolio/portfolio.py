"""Tracks cash, open positions, and trade history for a single book."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel

from .costs import CostModel, ZeroCost
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
    def __init__(self, portfolio_id: str, cash: float, cost_model: CostModel | None = None):
        self.id = portfolio_id
        self.cash = cash
        self._cost_model = cost_model or ZeroCost()
        self._positions: dict[str, Position] = {}
        self._trades: list[Trade] = []

    @property
    def tickers(self) -> list[str]:
        return list(self._positions.keys())

    @property
    def trades(self) -> list[Trade]:
        return list(self._trades)

    def position(self, ticker: str) -> Position:
        return self._positions.get(ticker, Position(ticker=ticker))

    def execute(self, order: Order, qty: int, price: float, as_of: date) -> None:
        """`qty` is the already-resolved share count -- Portfolio never looks at
        order.qty (which may be a percentage spec); see TradeGateway for resolution."""
        position = self._positions.setdefault(order.ticker, Position(ticker=order.ticker))
        notional = price * qty
        cost = self._cost_model.cost(order.side, qty, price)

        if order.side is Side.BUY:
            self.cash -= notional
            new_qty = position.qty + qty
            position.avg_price = (position.avg_price * position.qty + notional) / new_qty if new_qty else 0.0
            position.qty = new_qty
        else:
            self.cash += notional
            position.qty -= qty
        self.cash -= cost

        self._trades.append(Trade(date=as_of, ticker=order.ticker, side=order.side, qty=qty, price=price))

    def market_value(self, prices: dict[str, float]) -> float:
        holdings = sum(p.qty * prices.get(t, 0.0) for t, p in self._positions.items())
        return self.cash + holdings

    def get_state(self) -> dict:
        return {
            "cash": self.cash,
            "positions": {t: (p.qty, p.avg_price) for t, p in self._positions.items()},
            "trades": [trade.model_dump() for trade in self._trades],
        }

    def load_state(self, state: dict) -> None:
        self.cash = state["cash"]
        self._positions = {
            ticker: Position(ticker=ticker, qty=qty, avg_price=avg_price)
            for ticker, (qty, avg_price) in state["positions"].items()
        }
        self._trades = [Trade(**row) for row in state["trades"]]
