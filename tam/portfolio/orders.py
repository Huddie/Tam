"""Order request types passed to TradeGateway.stocks([...]). A pydantic model since
orders are built by user-authored strategy code, where catching a bad qty/side/ticker
at construction time (not silently mis-trading) matters most.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class QtyBasis(str, Enum):
    """Only meaningful for percentage-based sizing; ignored for static."""

    CASH = "cash"
    PORTFOLIO_VALUE = "portfolio_value"


class PriceBasis(str, Enum):
    """Which of the day's OHLC prices an order fills at. Defaults to CLOSE, the
    long-standing behavior -- OPEN exists for strategies that trade at the open
    (e.g. an overnight-hold sells at the next day's open before re-buying at
    that day's close)."""

    OPEN = "open"
    CLOSE = "close"


class Qty(BaseModel):
    """Order size: either a static share count, or a percentage resolved at fill
    time. For a BUY, pct is a percentage of `basis` (cash, or total portfolio
    value). For a SELL, pct is always a percentage of the currently held
    position (100 = sell the entire position) -- basis doesn't apply there.
    """

    static: Optional[int] = Field(default=None, gt=0)
    pct: Optional[float] = Field(default=None, gt=0, le=100)
    basis: QtyBasis = QtyBasis.CASH

    @model_validator(mode="after")
    def _exactly_one_of_static_or_pct(self) -> "Qty":
        if (self.static is None) == (self.pct is None):
            raise ValueError("Qty needs exactly one of static or pct")
        return self

    @classmethod
    def of(cls, value: "Qty | int | float | dict") -> "Qty":
        """Accept a plain number (-> static) or an already-built Qty/dict/dict-like
        (e.g. tam.config.DotDict, which doesn't subclass dict), so existing call
        sites that just pass an int keep working unchanged."""
        if isinstance(value, Qty):
            return value
        if hasattr(value, "items"):
            return cls(**dict(value.items()))
        return cls(static=int(value))


class Order(BaseModel):
    ticker: str = Field(min_length=1)
    side: Side
    qty: Qty
    portfolio: str = Field(min_length=1)
    price_basis: PriceBasis = PriceBasis.CLOSE

    @field_validator("qty", mode="before")
    @classmethod
    def _coerce_qty(cls, value):
        return Qty.of(value)
