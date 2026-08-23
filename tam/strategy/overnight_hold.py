"""Overnight-hold strategy: buys at each day's close and sells at the next
day's open, capturing only the close-to-open ("overnight") return while
staying flat during the regular session. General across tickers -- point it
at any symbol via config.
"""
from __future__ import annotations

from ..events.clock import EOD_TOPIC, OPEN_TOPIC
from ..events.types import Event, State
from ..portfolio.orders import Order, PriceBasis, Qty, Side
from ..registry import Registry
from .base import Strategy


class OvernightHoldStrategy(Strategy):
    def __init__(self, ticker: str, qty, portfolio_id: str):
        super().__init__()
        self._ticker = ticker
        self._qty = Qty.of(qty)
        self._portfolio_id = portfolio_id
        self._held = False

    def state_change(self, state: State) -> None:
        if state is State.RUNNING:
            self.subscribe_to(OPEN_TOPIC)
            self.subscribe_to(EOD_TOPIC)

    def on_event(self, event: Event) -> None:
        if event.type == OPEN_TOPIC:
            self._on_open()
        elif event.type == EOD_TOPIC:
            self._on_close()

    def _on_open(self) -> None:
        if not self._held:
            return
        self.trade.stocks(
            [
                Order(
                    ticker=self._ticker,
                    side=Side.SELL,
                    qty=Qty(pct=100),
                    portfolio=self._portfolio_id,
                    price_basis=PriceBasis.OPEN,
                )
            ]
        )
        self._held = False

    def _on_close(self) -> None:
        self.trade.stocks(
            [
                Order(
                    ticker=self._ticker,
                    side=Side.BUY,
                    qty=self._qty,
                    portfolio=self._portfolio_id,
                    price_basis=PriceBasis.CLOSE,
                )
            ]
        )
        self._held = True

    def get_state(self) -> dict:
        return {"held": self._held}

    def load_state(self, state: dict) -> None:
        self._held = state["held"]


@Registry.register(Strategy, "overnight_hold")
def build_overnight_hold(repository, portfolio_id: str, params, cash: float) -> OvernightHoldStrategy:
    """Config-driven adapter: defaults to fully investing available cash (qty:
    {pct: 100}) into each night's position, same convention as buy_and_hold."""
    ticker = params["ticker"]
    qty = params.get("qty", {"pct": 100})
    return OvernightHoldStrategy(ticker, qty, portfolio_id)
