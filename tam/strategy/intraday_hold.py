"""Intraday-hold strategy: buys at each day's open and sells at that same
day's close, capturing only the regular-session return while staying flat
overnight -- the mirror-image comparison to overnight_hold. General across
tickers -- point it at any symbol via config.
"""

from __future__ import annotations

from ..events.clock import EOD_TOPIC, OPEN_TOPIC
from ..events.types import Event, State
from ..portfolio.orders import Order, PriceBasis, Qty, Side
from ..registry import Registry
from .base import Strategy


class IntradayHoldStrategy(Strategy):
    def __init__(self, ticker: str, qty, portfolio_id: str):
        super().__init__()
        self._ticker = ticker
        self._qty = Qty.of(qty)
        self._portfolio_id = portfolio_id

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
        self.trade.stocks(
            [
                Order(
                    ticker=self._ticker,
                    side=Side.BUY,
                    qty=self._qty,
                    portfolio=self._portfolio_id,
                    price_basis=PriceBasis.OPEN,
                )
            ]
        )

    def _on_close(self) -> None:
        self.trade.stocks(
            [
                Order(
                    ticker=self._ticker,
                    side=Side.SELL,
                    qty=Qty(pct=100),
                    portfolio=self._portfolio_id,
                    price_basis=PriceBasis.CLOSE,
                )
            ]
        )


@Registry.register(Strategy, "intraday_hold")
def build_intraday_hold(repository, portfolio_id: str, params, cash: float) -> IntradayHoldStrategy:
    """Config-driven adapter: defaults to fully investing available cash (qty:
    {pct: 100}) into each day's position, same convention as buy_and_hold."""
    ticker = params["ticker"]
    qty = params.get("qty", {"pct": 100})
    return IntradayHoldStrategy(ticker, qty, portfolio_id)
