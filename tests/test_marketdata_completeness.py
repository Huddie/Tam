import json

import pandas as pd
import pytest

from tam.marketdata.completeness import compute_completeness, completeness_sidecar_suffix
from tam.marketdata.schema import MINUTE_BAR_COLUMNS, TS
from tam.marketdata.store import LocalMinuteBarStore


def _bars(timestamps, symbol="AAPL"):
    index = pd.DatetimeIndex(timestamps, tz="UTC", name=TS)
    n = len(timestamps)
    return pd.DataFrame(
        {
            "symbol": [symbol] * n,
            "open": [1.0] * n,
            "high": [1.0] * n,
            "low": [1.0] * n,
            "close": [1.0] * n,
            "volume": [100] * n,
            "adj_close": [1.0] * n,
            "transactions": [10] * n,
        },
        index=index,
    )[MINUTE_BAR_COLUMNS]


def test_full_session_on_a_regular_day_is_100_percent_complete():
    # 2024-01-02 is a regular NYSE session, 390 minutes (14:30-21:00 UTC).
    timestamps = pd.date_range("2024-01-02 14:30", "2024-01-02 20:59", freq="1min", tz="UTC")
    index = compute_completeness("AAPL", 2024, _bars(timestamps))

    jan = next(m for m in index.months if m.month == 1)
    day = next(d for d in jan.days if d.day == 2)
    assert day.actual_bars == 390
    assert day.expected_bars == 390


def test_a_short_session_is_reported_as_partial_not_rounded_away():
    df = _bars(["2024-01-02 14:30", "2024-01-02 14:31"])  # only 2 of 390 expected
    index = compute_completeness("AAPL", 2024, df)

    jan = next(m for m in index.months if m.month == 1)
    day = next(d for d in jan.days if d.day == 2)
    assert day.actual_bars == 2
    assert day.expected_bars == 390


def test_a_trading_day_with_zero_rows_still_appears_with_expected_bars():
    # No bars at all for 2024-01-02, but it's still a real NYSE session --
    # the day must show up with actual=0, not be silently absent.
    df = _bars(["2024-01-03 14:30"])
    index = compute_completeness("AAPL", 2024, df)

    jan = next(m for m in index.months if m.month == 1)
    jan_2 = next(d for d in jan.days if d.day == 2)
    assert jan_2.actual_bars == 0
    assert jan_2.expected_bars == 390


def test_extended_hours_bars_never_push_a_full_session_past_100_percent():
    # 2024-01-02's regular session is 14:30-21:00 UTC (390 minutes). Add
    # pre-market (13:00 UTC) and after-hours (21:30 UTC) bars on top of a
    # FULLY covered regular session -- actual_bars must stay at exactly
    # 390, not 392, since expected_bars is the regular session only and
    # comparing "every row that day" against it would push this over 100%
    # for a day that's actually perfectly complete. The 2 extended-hours
    # bars aren't discarded though -- they show up in their own separate
    # count instead.
    regular_session = list(pd.date_range("2024-01-02 14:30", "2024-01-02 20:59", freq="1min", tz="UTC"))
    extended_hours = [pd.Timestamp("2024-01-02 13:00", tz="UTC"), pd.Timestamp("2024-01-02 21:30", tz="UTC")]
    index = compute_completeness("AAPL", 2024, _bars(regular_session + extended_hours))

    jan = next(m for m in index.months if m.month == 1)
    day = next(d for d in jan.days if d.day == 2)
    assert day.actual_bars == 390
    assert day.expected_bars == 390
    assert day.extended_hours_bars == 2
    assert jan.extended_hours_bars == 2
    assert index.extended_hours_bars == 2


def test_weekends_and_holidays_are_not_counted_as_expected_trading_days():
    df = _bars(["2024-01-02 14:30"])
    index = compute_completeness("AAPL", 2024, df)

    # 2024-01-01 (New Year's Day, holiday) and 2024-01-06/07 (weekend)
    # should simply not appear in January's days at all.
    jan = next(m for m in index.months if m.month == 1)
    day_numbers = {d.day for d in jan.days}
    assert 1 not in day_numbers
    assert 6 not in day_numbers
    assert 7 not in day_numbers


def test_month_and_year_totals_sum_their_days():
    timestamps = list(pd.date_range("2024-01-02 14:30", "2024-01-02 15:29", freq="1min", tz="UTC")) + list(
        pd.date_range("2024-01-03 14:30", "2024-01-03 14:59", freq="1min", tz="UTC")
    )
    index = compute_completeness("AAPL", 2024, _bars(timestamps))

    jan = next(m for m in index.months if m.month == 1)
    assert jan.actual_bars == 60 + 30
    assert index.actual_bars == jan.actual_bars  # no other month has any actual rows...
    assert len(index.months) == 12  # ...but expected_bars still covers the WHOLE year's calendar
    assert index.expected_bars == sum(m.expected_bars for m in index.months)
    assert index.expected_bars > jan.expected_bars


def test_empty_dataframe_still_returns_a_full_calendar_of_expected_bars():
    index = compute_completeness("AAPL", 2024, _bars([]))
    assert index.actual_bars == 0
    assert index.expected_bars > 0
    assert any(d.expected_bars == 390 for m in index.months for d in m.days)


def test_to_json_round_trips_the_full_structure():
    df = _bars(["2024-01-02 14:30"])
    index = compute_completeness("AAPL", 2024, df)
    payload = json.loads(index.to_json())

    assert payload["symbol"] == "AAPL"
    assert payload["year"] == 2024
    assert payload["calendar"] == "NYSE"
    jan = next(m for m in payload["months"] if m["month"] == 1)
    day2 = next(d for d in jan["days"] if d["day"] == 2)
    assert day2 == {"day": 2, "actual_bars": 1, "expected_bars": 390, "extended_hours_bars": 0}


def test_returns_none_when_pandas_market_calendars_is_not_installed(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "pandas_market_calendars":
            raise ImportError("simulated: not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    assert compute_completeness("AAPL", 2024, _bars(["2024-01-02 14:30"])) is None


def test_store_write_produces_a_completeness_sidecar_next_to_the_parquet_file(tmp_path):
    store = LocalMinuteBarStore(tmp_path)
    df = _bars(["2024-01-02 14:30", "2024-01-02 14:31"])

    store.write("AAPL", df)

    sidecar = tmp_path / "AAPL" / f"2024{completeness_sidecar_suffix()}"
    assert sidecar.exists()
    payload = json.loads(sidecar.read_text())
    assert payload["actual_bars"] == 2


def test_store_write_recomputes_the_sidecar_from_the_full_merged_year_not_just_the_new_batch(tmp_path):
    store = LocalMinuteBarStore(tmp_path)
    store.write("AAPL", _bars(["2024-01-02 14:30"]))
    store.write("AAPL", _bars(["2024-01-03 14:30", "2024-01-03 14:31"]))

    sidecar = tmp_path / "AAPL" / f"2024{completeness_sidecar_suffix()}"
    payload = json.loads(sidecar.read_text())
    # Both days' rows must be reflected -- the second write's sidecar has
    # to be recomputed from the MERGED year, not just the day it just wrote.
    assert payload["actual_bars"] == 3


def test_store_skips_writing_a_sidecar_when_pandas_market_calendars_is_missing(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "pandas_market_calendars":
            raise ImportError("simulated: not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    store = LocalMinuteBarStore(tmp_path)
    store.write("AAPL", _bars(["2024-01-02 14:30"]))

    sidecar = tmp_path / "AAPL" / f"2024{completeness_sidecar_suffix()}"
    assert not sidecar.exists()
    # The actual data file must still be written normally either way.
    assert (tmp_path / "AAPL" / "2024.parquet").exists()
