"""Two-moving-average crossover strategy: no assumption about which window is
larger. Buys when MA(first_window) crosses below MA(second_window), sells when
it crosses back above. Order the two windows however produces the signal you
want — e.g. first_window=250, second_window=50 gives a classic golden cross
(buy when the 50-day average rises above the 250-day average).
"""
from __future__ import annotations

from ..data.repository import DataRepository
from ..events.clock import EOD_TOPIC
from ..events.types import Event, State
from ..portfolio.orders import Order, Qty, Side
from ..registry import Registry
from .base import Strategy
from .indicators import sma


class MACrossoverStrategy(Strategy):
    def __init__(
        self,
        repository: DataRepository,
        ticker: str,
        first_window: int,
        second_window: int,
        buy_qty,
        sell_qty,
        portfolio_id: str,
    ):
        super().__init__()
        self._repository = repository
        self._ticker = ticker
        self._first_window = first_window
        self._second_window = second_window
        self._buy_qty = Qty.of(buy_qty)
        self._sell_qty = Qty.of(sell_qty)
        self._portfolio_id = portfolio_id
        self._held = False

    def state_change(self, state: State) -> None:
        if state is State.RUNNING:
            self.subscribe_to(EOD_TOPIC)

    def on_event(self, event: Event) -> None:
        as_of = event.payload
        lookback = max(self._first_window, self._second_window)
        history = self._repository.query(self._ticker, end=as_of).tail(lookback)
        if len(history) < lookback:
            return

        close = history["close"]
        first = sma(close, self._first_window).iloc[-1]
        second = sma(close, self._second_window).iloc[-1]

        if first < second and not self._held:
            self.trade.stocks(
                [Order(ticker=self._ticker, side=Side.BUY, qty=self._buy_qty, portfolio=self._portfolio_id)]
            )
            self._held = True
        elif first > second and self._held:
            self.trade.stocks(
                [Order(ticker=self._ticker, side=Side.SELL, qty=self._sell_qty, portfolio=self._portfolio_id)]
            )
            self._held = False


@Registry.register(Strategy, "ma_crossover")
def build_ma_crossover(repository: DataRepository, portfolio_id: str, params, cash: float) -> MACrossoverStrategy:
    buy_qty = params["buy"]["qty"] if "buy" in params else params["qty"]
    sell_qty = params["sell"]["qty"] if "sell" in params else params["qty"]
    return MACrossoverStrategy(
        repository,
        params["ticker"],
        params["first_window"],
        params["second_window"],
        buy_qty,
        sell_qty,
        portfolio_id,
    )
