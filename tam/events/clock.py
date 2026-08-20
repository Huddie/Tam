"""Drives the backtest timeline: one end-of-day event per trading date."""
from __future__ import annotations

from datetime import date
from typing import List, Sequence

from .bus import EventBus
from .types import Event

EOD_TOPIC = "clock.eod"


class Clock:
    def __init__(self, dates: Sequence[date], bus: EventBus):
        self.dates: List[date] = sorted(dates)
        self._bus = bus

    def tick(self, current_date: date) -> None:
        self._bus.publish(EOD_TOPIC, Event(type=EOD_TOPIC, payload=current_date))
