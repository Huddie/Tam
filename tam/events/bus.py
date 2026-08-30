"""In-memory publish/subscribe hub used to fan events out to subscribed strategies."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from .types import Event

Handler = Callable[[Event], None]


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subscribers[topic].append(handler)

    def publish(self, topic: str, event: Event) -> None:
        for handler in list(self._subscribers.get(topic, ())):
            handler(event)
