"""Base class for user strategies; bound to the runtime by the harness before use."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Optional

from ..events.bus import EventBus
from ..events.types import ANNOTATION_TOPIC, Event, State
from ..portfolio.registry import PortfolioRegistry
from ..trading.gateway import TradeGateway


class Strategy(ABC):
    def __init__(self):
        self._bus: Optional[EventBus] = None
        self._trader: Optional[TradeGateway] = None
        self._portfolios: Optional[PortfolioRegistry] = None

    def bind(self, bus: EventBus, trader: TradeGateway, portfolios: PortfolioRegistry) -> None:
        self._bus = bus
        self._trader = trader
        self._portfolios = portfolios

    def subscribe_to(self, topic: str) -> None:
        self._bus.subscribe(topic, self.on_event)

    def publish(self, topic: str, event: Event) -> None:
        self._bus.publish(topic, event)

    def annotate(self, label: str, date: Optional[date] = None) -> None:
        """Mark a moment for the final report/live dashboard -- e.g. "fine-tuned
        to gen 3" -- rendered as a dotted vertical line on the equity chart.
        `date` defaults to the harness's current simulation date (from the
        bound TradeGateway) if not given. No-op before bind(), same as
        subscribe_to/publish -- there's no bus yet to emit onto."""
        if self._bus is None:
            return
        as_of = date if date is not None else (self._trader.current_date if self._trader else None)
        self.publish(ANNOTATION_TOPIC, Event(type=ANNOTATION_TOPIC, payload={"date": as_of, "label": label}))

    @property
    def trade(self) -> TradeGateway:
        return self._trader

    @property
    def portfolios(self) -> PortfolioRegistry:
        return self._portfolios

    @abstractmethod
    def state_change(self, state: State) -> None: ...

    @abstractmethod
    def on_event(self, event: Event) -> None: ...

    def get_state(self) -> dict:
        """Mutable state to persist across a checkpoint/resume, beyond what the
        constructor args already capture (those get rebuilt fresh from the same
        config on resume). Override for any field that changes during a run and
        isn't rederivable from a freshly constructed instance -- e.g. which side
        is currently held, an online model's learned weights, in-context memory.

        Must be pickle-safe with self-contained values, not live handles (a
        DataRepository, an HTTP client, a loaded model runtime) -- those get
        reinjected via the constructor when the strategy is rebuilt for resume.
        """
        return {}

    def load_state(self, state: dict) -> None:
        """Restore state previously returned by get_state(), applied on top of a
        freshly constructed instance built from the same config."""
