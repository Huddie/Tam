import pandas as pd
import pytest

from tam.marketdata.schema import MINUTE_BAR_COLUMNS, SYMBOL, TRANSACTIONS, TS, empty_minute_bar_frame, ensure_utc_index


def test_empty_minute_bar_frame_has_utc_tz_aware_index():
    frame = empty_minute_bar_frame()
    assert frame.empty
    assert list(frame.columns) == MINUTE_BAR_COLUMNS
    assert frame.index.name == TS
    assert str(frame.index.tz) == "UTC"


def test_ensure_utc_index_localizes_naive_timestamps_as_utc():
    df = pd.DataFrame({SYMBOL: ["AAPL"]}, index=pd.DatetimeIndex(["2024-01-02 14:30:00"]))
    result = ensure_utc_index(df)
    assert str(result.index.tz) == "UTC"
    assert result.index.name == TS
    assert result.index[0] == pd.Timestamp("2024-01-02 14:30:00", tz="UTC")


def test_ensure_utc_index_converts_other_tz_to_utc():
    naive = pd.Timestamp("2024-01-02 09:30:00")
    ny_index = pd.DatetimeIndex([naive]).tz_localize("America/New_York")
    df = pd.DataFrame({SYMBOL: ["AAPL"]}, index=ny_index)

    result = ensure_utc_index(df)

    assert str(result.index.tz) == "UTC"
    assert result.index[0] == naive.tz_localize("America/New_York").tz_convert("UTC")


@pytest.mark.parametrize("column", MINUTE_BAR_COLUMNS)
def test_minute_bar_columns_are_reused_from_eod_schema_where_applicable(column):
    # SYMBOL and TRANSACTIONS are new to minute bars (no EOD equivalent);
    # every OHLCV column is the SAME string tam.data.schema already defines,
    # not an independent redefinition that could silently drift out of sync.
    from tam.data import schema as eod_schema

    if column in (SYMBOL, TRANSACTIONS):
        return
    assert getattr(eod_schema, column.upper()) == column
