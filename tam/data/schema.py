"""Shared column names for OHLCV price history, so providers and stores agree on schema."""
import pandas as pd

DATE = "date"
OPEN = "open"
HIGH = "high"
LOW = "low"
CLOSE = "close"
ADJ_CLOSE = "adj_close"
VOLUME = "volume"

OHLCV_COLUMNS = [OPEN, HIGH, LOW, CLOSE, ADJ_CLOSE, VOLUME]


def empty_ohlcv_frame() -> "pd.DataFrame":
    index = pd.DatetimeIndex([], name=DATE)
    return pd.DataFrame(columns=OHLCV_COLUMNS, index=index)
