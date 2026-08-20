"""Reference baseline strategy: buys a fixed quantity on the first event, then holds."""
from __future__ import annotations

from ..events.clock import EOD_TOPIC
from ..events.types import Event, State
from ..portfolio.orders import Order, Qty, Side
from ..registry import Registry
from .base import Strategy


class BuyAndHoldStrategy(Strategy):
    def __init__(self, ticker: str, qty, portfolio_id: str):
        super().__init__()
        self._ticker = ticker
        self._qty = Qty.of(qty)
        self._portfolio_id = portfolio_id
        self._bought = False

    def state_change(self, state: State) -> None:
        if state is State.RUNNING:
            self.subscribe_to(EOD_TOPIC)

    def on_event(self, event: Event) -> None:
        if self._bought:
            return
        self.trade.stocks(
            [Order(ticker=self._ticker, side=Side.BUY, qty=self._qty, portfolio=self._portfolio_id)]
        )
        self._bought = True


@Registry.register(Strategy, "buy_and_hold")
def build_buy_and_hold(repository, portfolio_id: str, params, cash: float) -> BuyAndHoldStrategy:
    """Config-driven adapter: defaults to fully investing available cash (qty:
    {pct: 100}) when `params` has no qty, resolved at the actual buy date/price
    rather than approximated from the first close price at build time."""
    ticker = params["ticker"]
    qty = params.get("qty", {"pct": 100})
    return BuyAndHoldStrategy(ticker, qty, portfolio_id)
