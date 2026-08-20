"""Wires strategies, portfolios, and market data into a day-by-day event loop."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Dict, List, Optional, Sequence

from ..data.repository import DataRepository
from ..data.schema import CLOSE
from ..events.bus import EventBus
from ..events.clock import Clock
from ..events.types import State
from ..portfolio.portfolio import Portfolio
from ..portfolio.registry import PortfolioRegistry
from ..strategy.base import Strategy
from ..trading.gateway import TradeGateway
from .report import Report


@dataclass(frozen=True)
class Progress:
    """How far a run has gotten: day_index is 1-based, so day_index == total_days
    on the final simulated day."""

    day_index: int
    total_days: int
    current_date: date

    @property
    def fraction(self) -> float:
        return self.day_index / self.total_days if self.total_days else 1.0


OnProgress = Callable[[Progress], None]


class BacktestHarness:
    def __init__(
        self,
        repository: DataRepository,
        strategies: Sequence[Strategy],
        portfolios: Dict[str, Portfolio],
        dates: Sequence[date],
    ):
        self._repository = repository
        self._bus = EventBus()
        self._portfolios = PortfolioRegistry(portfolios)
        self._trader = TradeGateway(self._portfolios, self._price_on)
        self._strategies = list(strategies)
        for strategy in self._strategies:
            strategy.bind(self._bus, self._trader, self._portfolios)
        self._clock = Clock(dates, self._bus)

    def _price_on(self, ticker: str, as_of: date) -> float:
        history = self._repository.query(ticker, end=as_of)
        if history.empty:
            raise LookupError(f"no price data for {ticker} on or before {as_of}")
        return float(history.iloc[-1][CLOSE])

    def run(self, on_progress: Optional[OnProgress] = None) -> Report:
        for strategy in self._strategies:
            strategy.state_change(State.START)
        for strategy in self._strategies:
            strategy.state_change(State.RUNNING)

        snapshots: List[dict] = []
        total_days = len(self._clock.dates)
        for day_index, current_date in enumerate(self._clock.dates, start=1):
            self._trader.current_date = current_date
            self._clock.tick(current_date)
            snapshots.extend(self._snapshot(current_date))
            if on_progress is not None:
                on_progress(Progress(day_index, total_days, current_date))

        for strategy in self._strategies:
            strategy.state_change(State.END)

        return Report(snapshots, self._trades())

    def _trades(self) -> List[dict]:
        return [
            {
                "date": trade.date,
                "portfolio": portfolio_id,
                "ticker": trade.ticker,
                "side": trade.side,
                "qty": trade.qty,
                "price": trade.price,
            }
            for portfolio_id, portfolio in self._portfolios.items()
            for trade in portfolio.trades
        ]

    def _snapshot(self, as_of: date) -> List[dict]:
        rows = []
        for portfolio_id, portfolio in self._portfolios.items():
            prices = {ticker: self._price_on(ticker, as_of) for ticker in portfolio.tickers}
            rows.append(
                {
                    "date": as_of,
                    "portfolio": portfolio_id,
                    "cash": portfolio.cash,
                    "value": portfolio.market_value(prices),
                }
            )
        return rows
