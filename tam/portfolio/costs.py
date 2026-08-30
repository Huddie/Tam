"""Transaction cost models -- applied on every fill, both sides. Backward
compatible: Portfolio defaults to ZeroCost (today's exact behavior, no
change) unless a CostModel is given explicitly. Research doc's own
motivation (an overnight strategy trades twice a day, ~504 executions/year/
position -- tiny per-trade cost assumptions compound fast) is exactly why
this needs to be easy to swap and sweep (0bp/2bp/5bp/10bp/...), not hardcoded.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..registry import Registry
from .orders import Side


class CostModel(ABC):
    @abstractmethod
    def cost(self, side: Side, qty: int, price: float) -> float:
        """Dollar cost of one fill -- always >= 0, deducted from cash
        regardless of side (a cost is a cost whether buying or selling)."""


@Registry.register(CostModel, "zero")
class ZeroCost(CostModel):
    """Today's behavior before CostModel existed -- the default everywhere
    one isn't given explicitly."""

    def cost(self, side: Side, qty: int, price: float) -> float:
        return 0.0


@Registry.register(CostModel, "bps")
class BpsCost(CostModel):
    """A flat `rate` (e.g. 0.0005 for 5bps) of notional, per fill -- a round
    trip (buy then sell) costs `2 * rate` of notional, same as the research
    doc's own "test 0/2/5/10bps round trip" framing (round-trip bps here is
    2x this rate)."""

    def __init__(self, rate: float):
        self._rate = rate

    def cost(self, side: Side, qty: int, price: float) -> float:
        return abs(qty) * price * self._rate
