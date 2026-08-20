"""Base class for user strategies; bound to the runtime by the harness before use."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..events.bus import EventBus
from ..events.types import Event, State
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
