"""Shared column names for 1-minute OHLCV bars -- deliberately reuses
tam.data.schema's OPEN/HIGH/LOW/CLOSE/VOLUME/ADJ_CLOSE string constants
rather than redefining them, so a future rename stays in one place. Only
what's actually new for minute bars lives here: TS (the bar's start time,
timezone-AWARE UTC, never naive -- see the module docstring below for why)
and SYMBOL (minute bars are fetched/validated in bulk, many-symbols-per-day,
so unlike tam.data's one-symbol-per-frame convention, a symbol column travels
with the rows instead of being implied entirely by which file they're in).

Timestamps are stored as UTC, tz-aware, at the pandas/Arrow dtype level (not
as a naive "local" timestamp, and not as a string) -- market-hours boundaries
(9:30/16:00 America/New_York) cross a DST transition twice a year, at which
point a naive local timestamp is genuinely ambiguous (the same wall-clock
time occurs twice, or not at all). Convert to America/New_York only at
query/display time (`tz_convert`), never store it that way.
"""

from __future__ import annotations

import pandas as pd

from ..data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME

TS = "ts"
SYMBOL = "symbol"
TRANSACTIONS = "transactions"

# TRANSACTIONS (trade count per bar) isn't an OHLCV field, but it's a real
# column Massive's (and Polygon's) minute_aggs_v1 flat files actually carry
# -- kept end-to-end (fetch -> filter -> validate -> store -> query) rather
# than silently dropped, same reasoning as ADJ_CLOSE: a vendor that doesn't
# provide it just leaves it null (see _FlatFileS3Provider.fetch()), not a
# required field every vendor must supply.
MINUTE_BAR_COLUMNS = [SYMBOL, OPEN, HIGH, LOW, CLOSE, VOLUME, ADJ_CLOSE, TRANSACTIONS]

# Regular NYSE session length in minutes (9:30-16:00) -- used by
# tam.marketdata.validate to sanity-check bar counts per trading day. Half
# days (e.g. the day after Thanksgiving) are shorter; validate.py looks
# those up via pandas_market_calendars rather than assuming this constant
# applies to every session.
REGULAR_SESSION_MINUTES = 390


def empty_minute_bar_frame() -> pd.DataFrame:
    """Same shape write()/read() round-trip through: a tz-aware UTC
    DatetimeIndex named "ts" (NOT naive -- see module docstring), columns
    MINUTE_BAR_COLUMNS. Mirrors tam.data.schema.empty_ohlcv_frame()."""
    index = pd.DatetimeIndex([], name=TS, tz="UTC")
    return pd.DataFrame(columns=MINUTE_BAR_COLUMNS, index=index)


def ensure_utc_index(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce `df`'s index to a tz-aware UTC DatetimeIndex named TS --
    localizes a naive index to UTC (asserting that's actually what a caller
    meant, since a naive timestamp read back from a source with no explicit
    tz is a common bug, not a design choice) and converts an already-aware
    index in some other zone to UTC rather than silently keeping mixed
    zones across a store's partitions."""
    index = pd.DatetimeIndex(df.index)
    if index.tz is None:
        index = index.tz_localize("UTC")
    else:
        index = index.tz_convert("UTC")
    index.name = TS
    return df.set_axis(index, axis=0)
