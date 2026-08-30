"""Drives the backtest timeline: one open + one close event per trading date."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from .bus import EventBus
from .types import Event

OPEN_TOPIC = "clock.open"
EOD_TOPIC = "clock.eod"  # end-of-day / close tick -- name kept for backward compat


class Clock:
    def __init__(self, dates: Sequence[date], bus: EventBus):
        self.dates: list[date] = sorted(dates)
        self._bus = bus

    def tick(self, current_date: date) -> None:
        """Publishes the day's two ticks in order: OPEN_TOPIC (market open),
        then EOD_TOPIC (market close). A strategy that only cares about one
        side of the day just subscribes to that topic and ignores the other."""
        self._bus.publish(OPEN_TOPIC, Event(type=OPEN_TOPIC, payload=current_date))
        self._bus.publish(EOD_TOPIC, Event(type=EOD_TOPIC, payload=current_date))
