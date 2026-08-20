from datetime import date

import pandas as pd
import pytest

from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.schema import OHLCV_COLUMNS
from tam.data.storage import CsvStore, ParquetStore


class FakeProvider(DataProvider):
    """Deterministic in-memory provider so tests never touch the network."""

    def __init__(self, frame: pd.DataFrame):
        self._frame = frame
        self.calls = []

    def fetch_eod(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        df = self._frame
        return df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]


def _bars(dates, closes):
    index = pd.to_datetime(dates)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "adj_close": closes,
            "volume": [1_000] * len(closes),
        },
        index=index,
    ).rename_axis("date")[OHLCV_COLUMNS]


@pytest.mark.parametrize("store_cls", [CsvStore, ParquetStore])
def test_ingest_then_query_roundtrip(tmp_path, store_cls):
    frame = _bars(["2024-01-02", "2024-01-03", "2024-01-04"], [100.0, 101.0, 99.0])
    provider = FakeProvider(frame)
    store = store_cls(tmp_path)
    repo = DataRepository(provider, store)

    repo.ingest(["AAPL"], date(2024, 1, 2), date(2024, 1, 4))
    result = repo.query("AAPL")

    assert list(result["close"]) == [100.0, 101.0, 99.0]


@pytest.mark.parametrize("store_cls", [CsvStore, ParquetStore])
def test_reingest_fully_covered_range_skips_provider(tmp_path, store_cls):
    frame = _bars(["2024-01-02", "2024-01-03"], [100.0, 101.0])
    store = store_cls(tmp_path)
    provider = FakeProvider(frame)
    repo = DataRepository(provider, store)
    repo.ingest(["AAPL"], date(2024, 1, 2), date(2024, 1, 3))

    provider.calls.clear()
    repo.ingest(["AAPL"], date(2024, 1, 2), date(2024, 1, 3))

    assert provider.calls == []
    assert len(repo.query("AAPL")) == 2


@pytest.mark.parametrize("store_cls", [CsvStore, ParquetStore])
def test_reingest_wider_range_only_fetches_missing_tail(tmp_path, store_cls):
    frame = _bars(["2024-01-02", "2024-01-03"], [100.0, 101.0])
    store = store_cls(tmp_path)
    provider = FakeProvider(frame)
    repo = DataRepository(provider, store)
    repo.ingest(["AAPL"], date(2024, 1, 2), date(2024, 1, 3))

    wider = _bars(["2024-01-02", "2024-01-03", "2024-01-04"], [100.0, 101.0, 99.0])
    provider2 = FakeProvider(wider)
    repo2 = DataRepository(provider2, store)
    repo2.ingest(["AAPL"], date(2024, 1, 2), date(2024, 1, 4))

    # Only the uncovered tail (Jan 4) should have been requested from the provider.
    assert provider2.calls == [("AAPL", date(2024, 1, 4), date(2024, 1, 4))]
    result = repo2.query("AAPL")
    assert len(result) == 3
    assert result.loc["2024-01-04", "close"] == 99.0


def test_query_respects_date_bounds(tmp_path):
    frame = _bars(["2024-01-02", "2024-01-03", "2024-01-04"], [100.0, 101.0, 99.0])
    store = CsvStore(tmp_path)
    repo = DataRepository(FakeProvider(frame), store)
    repo.ingest(["AAPL"], date(2024, 1, 2), date(2024, 1, 4))

    result = repo.query("AAPL", start=date(2024, 1, 3), end=date(2024, 1, 3))
    assert len(result) == 1
    assert result.iloc[0]["close"] == 101.0


@pytest.mark.parametrize("store_cls,suffix", [(ParquetStore, ".parquet"), (CsvStore, ".csv")])
def test_store_partitions_on_disk_by_symbol_and_year(tmp_path, store_cls, suffix):
    frame = _bars(["2023-12-29", "2024-01-02"], [10.0, 12.0])
    store = store_cls(tmp_path)
    repo = DataRepository(FakeProvider(frame), store)

    repo.ingest(["AAPL"], date(2023, 12, 29), date(2024, 1, 2))

    assert (tmp_path / "AAPL" / f"2023{suffix}").exists()
    assert (tmp_path / "AAPL" / f"2024{suffix}").exists()


def test_query_on_never_ingested_symbol_returns_empty_frame_not_a_crash(tmp_path):
    store = CsvStore(tmp_path)
    repo = DataRepository(FakeProvider(_bars([], [])), store)

    result = repo.query("AAPL", start=date(2024, 1, 2), end=date(2024, 1, 4))

    assert result.empty
    assert isinstance(result.index, pd.DatetimeIndex)


def test_ingest_warns_when_provider_returns_no_data_for_a_gap(tmp_path):
    store = CsvStore(tmp_path)
    repo = DataRepository(FakeProvider(_bars([], [])), store)

    with pytest.warns(UserWarning, match="no data returned for AAPL"):
        repo.ingest(["AAPL"], date(2024, 1, 2), date(2024, 1, 4))


def test_ingest_across_years_only_rewrites_the_touched_year_partition(tmp_path):
    store = ParquetStore(tmp_path)
    written_paths = []
    original_write_file = store._write_file
    store._write_file = lambda path, df: (written_paths.append(path), original_write_file(path, df))[1]

    frame_2023 = _bars(["2023-12-29", "2023-12-30"], [10.0, 11.0])
    DataRepository(FakeProvider(frame_2023), store).ingest(["AAPL"], date(2023, 12, 29), date(2023, 12, 30))

    written_paths.clear()

    frame_2024 = _bars(["2024-01-02"], [12.0])
    DataRepository(FakeProvider(frame_2024), store).ingest(["AAPL"], date(2024, 1, 2), date(2024, 1, 2))

    assert written_paths == [tmp_path / "AAPL" / "2024.parquet"]
