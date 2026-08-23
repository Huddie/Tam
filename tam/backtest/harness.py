"""Wires strategies, portfolios, and market data into a day-by-day event loop."""
from __future__ import annotations

import os
import pickle
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from ..data.repository import DataRepository
from ..data.schema import CLOSE, OPEN
from ..events.bus import EventBus
from ..events.clock import Clock
from ..events.types import ANNOTATION_TOPIC, State
from ..portfolio.orders import PriceBasis
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
        self._annotations: List[dict] = []
        self._bus.subscribe(ANNOTATION_TOPIC, self._on_annotation)
        self._portfolios = PortfolioRegistry(portfolios)
        self._trader = TradeGateway(self._portfolios, self._price_on)
        self._strategies = list(strategies)
        for strategy in self._strategies:
            strategy.bind(self._bus, self._trader, self._portfolios)
        self._clock = Clock(dates, self._bus)

    def _on_annotation(self, event) -> None:
        self._annotations.append(dict(event.payload))

    def _price_on(self, ticker: str, as_of: date, basis: PriceBasis = PriceBasis.CLOSE) -> float:
        history = self._repository.query(ticker, end=as_of)
        if history.empty:
            raise LookupError(f"no price data for {ticker} on or before {as_of}")
        column = OPEN if basis is PriceBasis.OPEN else CLOSE
        return float(history.iloc[-1][column])

    def run(
        self,
        on_progress: Optional[OnProgress] = None,
        checkpoint_path: Optional[str] = None,
        checkpoint_every: int = 1,
    ) -> Report:
        """Run the full date range. If checkpoint_path is given: resume from it if
        it already exists (skipping every day already completed), and write a
        fresh checkpoint every `checkpoint_every` completed days -- so a crash
        (bad data, a flaky external model server, anything) loses at most that
        many days of work, not the whole run. The checkpoint is removed on a
        clean finish, since it exists purely to resume an interrupted run of
        THIS exact strategies/portfolios/dates configuration -- rerunning the
        same config from scratch after success should start fresh, not replay
        a stale checkpoint from a previous, unrelated run.
        """
        snapshots: List[dict] = []
        completed_days = 0
        if checkpoint_path is not None and Path(checkpoint_path).exists():
            completed_days, snapshots, self._annotations = self._load_checkpoint(checkpoint_path)

        for strategy in self._strategies:
            strategy.state_change(State.START)
        for strategy in self._strategies:
            strategy.state_change(State.RUNNING)

        total_days = len(self._clock.dates)
        for day_index, current_date in enumerate(self._clock.dates, start=1):
            if day_index <= completed_days:
                continue
            self._trader.current_date = current_date
            self._clock.tick(current_date)
            snapshots.extend(self._snapshot(current_date))
            if on_progress is not None:
                on_progress(Progress(day_index, total_days, current_date))
            if checkpoint_path is not None and day_index % checkpoint_every == 0:
                self._write_checkpoint(checkpoint_path, day_index, snapshots)

        for strategy in self._strategies:
            strategy.state_change(State.END)

        if checkpoint_path is not None:
            Path(checkpoint_path).unlink(missing_ok=True)

        return Report(snapshots, self._trades(), self._annotations)

    def _write_checkpoint(self, checkpoint_path: str, day_index: int, snapshots: List[dict]) -> None:
        state = {
            "day_index": day_index,
            "snapshots": snapshots,
            "annotations": list(self._annotations),
            "portfolios": {portfolio_id: p.get_state() for portfolio_id, p in self._portfolios.items()},
            "strategies": [s.get_state() for s in self._strategies],
        }
        path = Path(checkpoint_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a crash mid-write can't corrupt the last good checkpoint.
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        with os.fdopen(fd, "wb") as handle:
            pickle.dump(state, handle)
        os.replace(tmp_name, path)

    def _load_checkpoint(self, checkpoint_path: str) -> tuple[int, List[dict], List[dict]]:
        with open(checkpoint_path, "rb") as handle:
            state = pickle.load(handle)
        for portfolio_id, portfolio_state in state["portfolios"].items():
            self._portfolios[portfolio_id].load_state(portfolio_state)
        for strategy, strategy_state in zip(self._strategies, state["strategies"]):
            strategy.load_state(strategy_state)
        return state["day_index"], state["snapshots"], state.get("annotations", [])

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
