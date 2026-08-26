import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")

from tam.marketdata.schema import MINUTE_BAR_COLUMNS
from tam.marketdata.store import LocalMinuteBarStore
from tam.marketdata.duckdb_query import open_duckdb


def _write_spy_bars(local_root, day="2024-01-02", periods=10, start_close=100.0):
    store = LocalMinuteBarStore(f"{local_root}/minute")
    index = pd.date_range(f"{day} 14:30", periods=periods, freq="min", tz="UTC", name="ts")
    closes = [start_close + i for i in range(periods)]
    df = pd.DataFrame(
        {
            "symbol": ["SPY"] * periods,
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [1000] * periods,
            "adj_close": closes,
            "transactions": [50] * periods,
        },
        index=index,
    )[MINUTE_BAR_COLUMNS]
    store.write("SPY", df)


def test_open_duckdb_with_local_root_reads_minute_bars(tmp_path):
    _write_spy_bars(tmp_path)

    con = open_duckdb(local_root=str(tmp_path))
    result = con.sql("SELECT * FROM minute_bars('SPY') ORDER BY ts").df()

    assert len(result) == 10
    assert list(result["symbol"]) == ["SPY"] * 10


def test_daily_bars_aggregates_a_full_session(tmp_path):
    _write_spy_bars(tmp_path, periods=10, start_close=100.0)

    con = open_duckdb(local_root=str(tmp_path))
    result = con.sql("SELECT * FROM daily_bars('SPY')").df()

    assert len(result) == 1
    row = result.iloc[0]
    assert row["open"] == 100.0
    assert row["close"] == 109.0
    assert row["high"] == 109.5
    assert row["low"] == 99.5
    assert row["volume"] == 10_000


def test_rollup_bars_buckets_by_the_requested_interval(tmp_path):
    _write_spy_bars(tmp_path, periods=10, start_close=0.0)

    con = open_duckdb(local_root=str(tmp_path))
    result = con.sql("SELECT * FROM rollup_bars('SPY', 5) ORDER BY bucket").df()

    assert len(result) == 2
    assert result.iloc[0]["open"] == 0.0
    assert result.iloc[0]["close"] == 4.0
    assert result.iloc[1]["open"] == 5.0
    assert result.iloc[1]["close"] == 9.0


def test_daily_returns_computes_close_to_close_pct_change(tmp_path):
    _write_spy_bars(tmp_path, day="2024-01-02", periods=1, start_close=100.0)
    _write_spy_bars(tmp_path, day="2024-01-03", periods=1, start_close=110.0)

    con = open_duckdb(local_root=str(tmp_path))
    result = con.sql("SELECT * FROM daily_returns('SPY') ORDER BY day").df()

    assert result.iloc[0]["return"] != result.iloc[0]["return"]  # NaN -- no prior day
    assert result.iloc[1]["return"] == pytest.approx(0.10)


def test_rolling_volatility_is_null_until_the_window_fills(tmp_path):
    for i, day in enumerate(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]):
        _write_spy_bars(tmp_path, day=day, periods=1, start_close=100.0 + i)

    con = open_duckdb(local_root=str(tmp_path))
    result = con.sql("SELECT * FROM rolling_volatility('SPY', 3) ORDER BY day").df()

    assert pd.isna(result.iloc[0]["annualized_vol"])
    assert pd.isna(result.iloc[1]["annualized_vol"])
    assert not pd.isna(result.iloc[2]["annualized_vol"])
