"""Buys when price crosses above its own N-day moving average, sells when it
crosses back below (gated by a held flag so it only fires on the transition).
"""
from __future__ import annotations

from ..data.repository import DataRepository
from ..events.clock import EOD_TOPIC
from ..events.types import Event, State
from ..portfolio.orders import Order, Qty, Side
from ..registry import Registry
from .base import Strategy


class MovingAverageStrategy(Strategy):
    def __init__(
        self, repository: DataRepository, ticker: str, window: int, buy_qty, sell_qty, portfolio_id: str
    ):
        super().__init__()
        self._repository = repository
        self._ticker = ticker
        self._window = window
        self._buy_qty = Qty.of(buy_qty)
        self._sell_qty = Qty.of(sell_qty)
        self._portfolio_id = portfolio_id
        self._held = False

    def state_change(self, state: State) -> None:
        if state is State.RUNNING:
            self.subscribe_to(EOD_TOPIC)

    def on_event(self, event: Event) -> None:
        as_of = event.payload
        history = self._repository.query(self._ticker, end=as_of).tail(self._window)
        if len(history) < self._window:
            return

        moving_average = history["close"].mean()
        price = history["close"].iloc[-1]

        if price > moving_average and not self._held:
            self.trade.stocks(
                [Order(ticker=self._ticker, side=Side.BUY, qty=self._buy_qty, portfolio=self._portfolio_id)]
            )
            self._held = True
        elif price < moving_average and self._held:
            self.trade.stocks(
                [Order(ticker=self._ticker, side=Side.SELL, qty=self._sell_qty, portfolio=self._portfolio_id)]
            )
            self._held = False


@Registry.register(Strategy, "moving_average")
def build_moving_average(repository: DataRepository, portfolio_id: str, params, cash: float) -> MovingAverageStrategy:
    buy_qty = params["buy"]["qty"] if "buy" in params else params["qty"]
    sell_qty = params["sell"]["qty"] if "sell" in params else params["qty"]
    return MovingAverageStrategy(repository, params["ticker"], params["window"], buy_qty, sell_qty, portfolio_id)
