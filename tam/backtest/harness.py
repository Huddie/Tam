"""Wires strategies, portfolios, and market data into a day-by-day event loop."""

from __future__ import annotations

import os
import pickle
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from ..data.repository import DataRepository
from ..events.bus import EventBus
from ..events.clock import Clock
from ..events.types import ANNOTATION_TOPIC, State
from ..portfolio.orders import PRICE_BASIS_COLUMN, PriceBasis
from ..portfolio.portfolio import Portfolio
from ..portfolio.registry import PortfolioRegistry
from ..registry import RunRegistry
from ..strategy.base import Strategy
from ..trading.gateway import TradeGateway
from ..trading.trader import Trader
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
        portfolios: dict[str, Portfolio],
        dates: Sequence[date],
        traders: Sequence[Trader] | None = None,
    ):
        self._repository = repository
        self._bus = EventBus()
        self._annotations: list[dict] = []
        self._bus.subscribe(ANNOTATION_TOPIC, self._on_annotation)
        self._portfolios = PortfolioRegistry(portfolios)
        self._trader = TradeGateway(self._portfolios, self._price_on)
        self._strategies = list(strategies)
        for strategy in self._strategies:
            strategy.bind(self._bus, self._trader, self._portfolios)
        self._clock = Clock(dates, self._bus)

        self.traders = list(traders or [])
        self.runtime = RunRegistry()
        for trader in self.traders:
            self.runtime.put(Trader, trader.name, trader)
            self.runtime.put(Strategy, trader.name, trader.strategy)

    def _on_annotation(self, event) -> None:
        self._annotations.append(dict(event.payload))

    def _price_on(self, ticker: str, as_of: date, basis: PriceBasis = PriceBasis.CLOSE) -> float:
        try:
            return self._repository.history(ticker).price_at(as_of, PRICE_BASIS_COLUMN[basis])
        except LookupError as exc:
            raise LookupError(f"no price data for {ticker} on or before {as_of}") from exc

    def run(
        self,
        on_progress: OnProgress | None = None,
        checkpoint_path: str | None = None,
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
        snapshots: list[dict] = []
        completed_days = 0
        if checkpoint_path is not None and Path(checkpoint_path).exists():
            completed_days, snapshots, self._annotations = self._load_checkpoint(checkpoint_path)

        for strategy in self._strategies:
            strategy.state_change(State.START)
        for strategy in self._strategies:
            strategy.state_change(State.RUNNING)

        if completed_days == 0 and self._clock.dates:
            # A pre-trade anchor point, dated the day before the first
            # trading day -- portfolios are freshly built with nothing but
            # their configured starting cash at this point (state_change
            # above only subscribes strategies to topics; no trades fire
            # until the first Clock.tick below). Without this, a strategy
            # that round-trips within day 1 (e.g. intraday_hold: buy at
            # open, sell at close) would have its FIRST plotted/summarized
            # value already reflect day 1's return, making its "start_value"
            # silently diverge from the cash actually configured while
            # other strategies' (which don't fully round-trip before the
            # first snapshot) start_value coincidentally matches it.
            first_date = self._clock.dates[0]
            snapshots.extend(self._snapshot(first_date - timedelta(days=1), price_date=first_date))

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

    def _write_checkpoint(self, checkpoint_path: str, day_index: int, snapshots: list[dict]) -> None:
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

    def _load_checkpoint(self, checkpoint_path: str) -> tuple[int, list[dict], list[dict]]:
        with open(checkpoint_path, "rb") as handle:
            state = pickle.load(handle)
        for portfolio_id, portfolio_state in state["portfolios"].items():
            self._portfolios[portfolio_id].load_state(portfolio_state)
        for strategy, strategy_state in zip(self._strategies, state["strategies"]):
            strategy.load_state(strategy_state)
        return state["day_index"], state["snapshots"], state.get("annotations", [])

    def _trades(self) -> list[dict]:
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

    def _snapshot(self, as_of: date, price_date: date | None = None) -> list[dict]:
        """`price_date` (defaults to `as_of`) is what's used to look up mark-to-
        market prices -- distinct from `as_of` only for the pre-trade anchor
        snapshot, which is labeled a day before the data range starts (so no
        price history exists there yet) but still needs to price whatever a
        portfolio already holds at that point."""
        price_date = price_date if price_date is not None else as_of
        rows = []
        for portfolio_id, portfolio in self._portfolios.items():
            prices = {ticker: self._price_on(ticker, price_date) for ticker in portfolio.tickers}
            rows.append(
                {
                    "date": as_of,
                    "portfolio": portfolio_id,
                    "cash": portfolio.cash,
                    "value": portfolio.market_value(prices),
                }
            )
        return rows
