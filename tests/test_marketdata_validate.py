from datetime import date

import pandas as pd
import pytest

from tam.marketdata.schema import MINUTE_BAR_COLUMNS
from tam.marketdata.validate import validate_day

pytest.importorskip("pandas_market_calendars")


def _bars(rows, symbol="AAPL"):
    """rows: list of (ts_str, open, high, low, close, volume)."""
    index = pd.DatetimeIndex([r[0] for r in rows], tz="UTC", name="ts")
    return pd.DataFrame(
        {
            "symbol": [symbol] * len(rows),
            "open": [r[1] for r in rows],
            "high": [r[2] for r in rows],
            "low": [r[3] for r in rows],
            "close": [r[4] for r in rows],
            "volume": [r[5] for r in rows],
            "adj_close": [r[4] for r in rows],
            "transactions": [5] * len(rows),
        },
        index=index,
    )[MINUTE_BAR_COLUMNS]


def _full_session(day: str, symbol="AAPL"):
    index = pd.date_range(f"{day} 14:30:00", periods=390, freq="min", tz="UTC")
    return pd.DataFrame(
        {
            "symbol": symbol,
            "open": 1.0,
            "high": 1.2,
            "low": 0.9,
            "close": 1.1,
            "volume": 10,
            "adj_close": 1.1,
            "transactions": 5,
        },
        index=index.rename("ts"),
    )[MINUTE_BAR_COLUMNS]


def test_empty_frame_is_valid_with_no_findings():
    report = validate_day(pd.DataFrame(columns=MINUTE_BAR_COLUMNS), date(2024, 1, 2))
    assert report.ok
    assert report.errors == []


def test_full_regular_session_on_a_trading_day_is_valid():
    report = validate_day(_full_session("2024-01-02"), date(2024, 1, 2))
    assert report.ok
    assert report.warning_messages == []


def test_high_below_max_of_open_close_low_is_an_error():
    df = _bars([("2024-01-02 14:30", 1.0, 0.5, 0.9, 1.1, 10)])  # high < close
    report = validate_day(df, date(2024, 1, 2))
    assert not report.ok
    assert any("high" in message for message in report.errors)


def test_low_above_min_of_open_close_high_is_an_error():
    df = _bars([("2024-01-02 14:30", 1.0, 1.2, 1.15, 1.1, 10)])  # low > close
    report = validate_day(df, date(2024, 1, 2))
    assert not report.ok
    assert any("low" in message for message in report.errors)


def test_negative_volume_is_an_error():
    df = _bars([("2024-01-02 14:30", 1.0, 1.2, 0.9, 1.1, -5)])
    report = validate_day(df, date(2024, 1, 2))
    assert not report.ok
    assert any("volume" in message for message in report.errors)


def test_non_positive_price_is_an_error():
    df = _bars([("2024-01-02 14:30", 0.0, 0.0, 0.0, 0.0, 10)])
    report = validate_day(df, date(2024, 1, 2))
    assert not report.ok
    assert any("non-positive" in message for message in report.errors)


def test_duplicate_timestamp_within_a_symbol_is_an_error():
    df = _bars([("2024-01-02 14:30", 1.0, 1.1, 0.9, 1.0, 10), ("2024-01-02 14:30", 1.0, 1.1, 0.9, 1.0, 10)])
    report = validate_day(df, date(2024, 1, 2))
    assert not report.ok
    assert any("duplicate" in message for message in report.errors)


def test_unsorted_timestamps_within_a_symbol_is_an_error():
    df = _bars([("2024-01-02 14:31", 1.0, 1.1, 0.9, 1.0, 10), ("2024-01-02 14:30", 1.0, 1.1, 0.9, 1.0, 10)])
    report = validate_day(df, date(2024, 1, 2))
    assert not report.ok
    assert any("sorted" in message for message in report.errors)


def test_thin_session_is_a_warning_not_an_error():
    thin = _full_session("2024-01-02").iloc[:50]  # well under half the expected session
    report = validate_day(thin, date(2024, 1, 2))
    assert report.ok  # warning, not an error -- doesn't fail the day
    assert any("50/390" in message for message in report.warning_messages)


def test_data_on_a_non_trading_day_is_an_error():
    # 2024-01-06 is a Saturday -- NYSE isn't open
    df = _full_session("2024-01-06")
    report = validate_day(df, date(2024, 1, 6))
    assert not report.ok
    assert any("not a NYSE trading day" in message for message in report.errors)


def test_raise_if_invalid_raises_with_all_errors_listed():
    df = _bars([("2024-01-02 14:30", 0.0, 0.0, 0.0, 0.0, -5)])
    report = validate_day(df, date(2024, 1, 2))

    with pytest.raises(ValueError, match="2024-01-02"):
        report.raise_if_invalid()


def test_raise_if_invalid_is_a_noop_when_valid():
    report = validate_day(_full_session("2024-01-02"), date(2024, 1, 2))
    report.raise_if_invalid()  # must not raise
