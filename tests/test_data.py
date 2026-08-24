from datetime import date

import pandas as pd
import pytest

from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.schema import OHLCV_COLUMNS
from tam.data.storage import CsvStore, ParquetStore
from tam.data.writer import CsvRepoWriter, ParquetRepoWriter, RepoWriter
from tam.registry import Registry


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


@pytest.mark.parametrize("store_cls", [CsvStore, ParquetStore])
def test_history_reads_store_at_most_once_per_symbol(tmp_path, store_cls):
    frame = _bars(["2024-01-02", "2024-01-03", "2024-01-04"], [100.0, 101.0, 99.0])
    store = store_cls(tmp_path)
    repo = DataRepository(FakeProvider(frame), store)
    repo.ingest(["AAPL"], date(2024, 1, 2), date(2024, 1, 4))

    read_calls = []
    original_read = store.read
    store.read = lambda symbol: (read_calls.append(symbol), original_read(symbol))[1]

    repo.history("AAPL")
    repo.history("AAPL")
    repo.query("AAPL")

    assert read_calls == ["AAPL"]


def test_ingest_invalidates_cached_history(tmp_path):
    frame = _bars(["2024-01-02", "2024-01-03"], [100.0, 101.0])
    store = CsvStore(tmp_path)
    provider = FakeProvider(frame)
    repo = DataRepository(provider, store)
    repo.ingest(["AAPL"], date(2024, 1, 2), date(2024, 1, 3))

    repo.history("AAPL")  # warms the cache

    wider = _bars(["2024-01-02", "2024-01-03", "2024-01-04"], [100.0, 101.0, 99.0])
    provider._frame = wider
    repo.ingest(["AAPL"], date(2024, 1, 2), date(2024, 1, 4))

    assert list(repo.history("AAPL").frame["close"]) == [100.0, 101.0, 99.0]


def test_query_returns_an_independent_copy_not_the_cached_frame(tmp_path):
    frame = _bars(["2024-01-02", "2024-01-03"], [100.0, 101.0])
    store = CsvStore(tmp_path)
    repo = DataRepository(FakeProvider(frame), store)
    repo.ingest(["AAPL"], date(2024, 1, 2), date(2024, 1, 3))

    result = repo.query("AAPL")
    result.iloc[0, result.columns.get_loc("close")] = -1.0

    assert repo.history("AAPL").frame.iloc[0]["close"] == 100.0


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


def test_repo_writer_registry_has_csv_and_parquet_built_in():
    assert set(Registry.names(RepoWriter)) >= {"csv", "parquet"}


class _RecordingRepoWriter(RepoWriter):
    """A RepoWriter that isn't a file at all -- proof that DataRepository.write()
    doesn't assume a destination shape, just hands over {symbol: DataFrame}."""

    def __init__(self):
        self.received = None

    def write(self, data):
        self.received = data
        return {symbol: len(df) for symbol, df in data.items()}


def test_repository_write_hands_every_symbols_full_history_to_the_writer(tmp_path):
    aapl = _bars(["2024-01-02", "2024-01-03"], [100.0, 101.0])
    msft = _bars(["2024-01-02", "2024-01-03", "2024-01-04"], [200.0, 201.0, 202.0])
    store = CsvStore(tmp_path)

    class _TwoSymbolProvider(DataProvider):
        def fetch_eod(self, symbol, start, end):
            frame = aapl if symbol == "AAPL" else msft
            return frame[(frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))]

    repo = DataRepository(_TwoSymbolProvider(), store)
    repo.ingest(["AAPL", "MSFT"], date(2024, 1, 2), date(2024, 1, 4))

    writer = _RecordingRepoWriter()
    result = repo.write(writer, ["AAPL", "MSFT"])

    assert set(writer.received) == {"AAPL", "MSFT"}
    assert list(writer.received["AAPL"]["close"]) == [100.0, 101.0]
    assert result == {"AAPL": 2, "MSFT": 3}


@pytest.mark.parametrize("writer_cls,suffix,reader", [
    (CsvRepoWriter, ".csv", pd.read_csv),
    (ParquetRepoWriter, ".parquet", pd.read_parquet),
])
def test_flat_file_repo_writers_write_one_file_per_symbol(tmp_path, writer_cls, suffix, reader):
    frame = _bars(["2024-01-02", "2024-01-03"], [100.0, 101.0])
    store = CsvStore(tmp_path / "cache")
    repo = DataRepository(FakeProvider(frame), store)
    repo.ingest(["AAPL"], date(2024, 1, 2), date(2024, 1, 3))

    out_root = tmp_path / "out"
    paths = repo.write(writer_cls(out_root), ["AAPL"])

    assert paths == {"AAPL": out_root / f"AAPL{suffix}"}
    assert list(reader(paths["AAPL"])["close"]) == [100.0, 101.0]
