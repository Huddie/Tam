"""Core event/lifecycle types shared across the event bus, clock, and strategies."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any


class State(Enum):
    START = auto()
    RUNNING = auto()
    END = auto()


@dataclass(frozen=True)
class Event:
    type: str
    payload: Any = None


# A strategy publishes here (via Strategy.annotate) to mark a moment on the
# final report/live dashboard -- e.g. "fine-tuned to gen 3" -- as a dotted
# vertical line on the equity chart. BacktestHarness is the one subscriber,
# collecting payloads into Report.annotations; see harness.py.
ANNOTATION_TOPIC = "annotation"
