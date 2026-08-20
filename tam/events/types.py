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
