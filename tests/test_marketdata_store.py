import pandas as pd
import pytest

from tam.marketdata.schema import MINUTE_BAR_COLUMNS, TS
from tam.marketdata.store import LocalMinuteBarStore
from tam.registry import Registry
from tam.marketdata.store import MinuteBarStore


def _bars(timestamps, closes, symbol="AAPL"):
    index = pd.DatetimeIndex(timestamps, tz="UTC", name=TS)
    return pd.DataFrame(
        {
            "symbol": [symbol] * len(closes),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [100] * len(closes),
            "adj_close": closes,
            "transactions": [10] * len(closes),
        },
        index=index,
    )[MINUTE_BAR_COLUMNS]


def test_exists_is_false_before_any_write(tmp_path):
    store = LocalMinuteBarStore(tmp_path)
    assert store.exists("AAPL") is False
    assert store.read("AAPL").empty


def test_write_then_read_roundtrip(tmp_path):
    store = LocalMinuteBarStore(tmp_path)
    df = _bars(["2024-01-02 14:30", "2024-01-02 14:31"], [100.0, 101.0])

    store.write("AAPL", df)

    assert store.exists("aapl") is True  # symbol lookup is case-insensitive
    result = store.read("AAPL")
    assert list(result["close"]) == [100.0, 101.0]
    assert str(result.index.tz) == "UTC"


def test_write_splits_across_year_partitions_transparently(tmp_path):
    store = LocalMinuteBarStore(tmp_path)
    df = _bars(["2020-06-01 14:30", "2021-06-01 14:30", "2022-06-01 14:30"], [1.0, 2.0, 3.0])

    store.write("AAPL", df)

    years = store._partition_years("AAPL")
    assert years == [2020, 2021, 2022]
    result = store.read("AAPL")
    assert list(result["close"]) == [1.0, 2.0, 3.0]


def test_write_is_upsert_overwriting_overlapping_timestamps_and_adding_new_ones(tmp_path):
    store = LocalMinuteBarStore(tmp_path)
    store.write("AAPL", _bars(["2024-01-02 14:30", "2024-01-02 14:31"], [100.0, 101.0]))

    store.write("AAPL", _bars(["2024-01-02 14:31", "2024-01-02 14:32"], [999.0, 102.0]))

    result = store.read("AAPL")
    assert list(result["close"]) == [100.0, 999.0, 102.0]  # overlap keeps the NEW value, new row appended
    assert result.index.is_monotonic_increasing
    assert not result.index.has_duplicates


def test_write_of_empty_frame_is_a_noop(tmp_path):
    store = LocalMinuteBarStore(tmp_path)
    from tam.marketdata.schema import empty_minute_bar_frame

    store.write("AAPL", empty_minute_bar_frame())

    assert store.exists("AAPL") is False


def test_different_symbols_are_stored_independently(tmp_path):
    store = LocalMinuteBarStore(tmp_path)
    store.write("AAPL", _bars(["2024-01-02 14:30"], [100.0], symbol="AAPL"))
    store.write("MSFT", _bars(["2024-01-02 14:30"], [200.0], symbol="MSFT"))

    assert list(store.read("AAPL")["close"]) == [100.0]
    assert list(store.read("MSFT")["close"]) == [200.0]


def test_symbol_column_is_preserved_through_a_write_read_roundtrip(tmp_path):
    store = LocalMinuteBarStore(tmp_path)
    store.write("AAPL", _bars(["2024-01-02 14:30"], [100.0], symbol="AAPL"))

    result = store.read("AAPL")
    assert list(result["symbol"]) == ["AAPL"]


def test_local_and_r2_stores_are_registered_under_expected_names():
    assert "local_parquet" in Registry.names(MinuteBarStore)
    assert "r2_parquet" in Registry.names(MinuteBarStore)


def test_r2_store_construction_raises_actionable_error_without_credentials(monkeypatch):
    for var in ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"]:
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(RuntimeError, match="Missing R2 credential"):
        Registry.create(MinuteBarStore, "r2_parquet")
