import gzip
import os
from datetime import date, timedelta

import pandas as pd
import pytest
from pyarrow import fs

from tam.basket.universe import StaticUniverse, UniverseProvider
from tam.marketdata.ingest import IngestResult, ingest, ingest_day, run_ingest
from tam.marketdata.providers import MinuteBarProvider, _FlatFileS3Provider
from tam.marketdata.store import LocalMinuteBarStore, MinuteBarStore
from tam.registry import Registry


class _LocalFlatFileProvider(_FlatFileS3Provider):
    def __init__(self, root):
        super().__init__(
            endpoint="unused",
            bucket=str(root),
            key_template="%Y-%m-%d.csv.gz",
            column_map={
                "ticker": "symbol",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
            },
            timestamp_column="window_start",
            timestamp_unit="ns",
            access_key_id="unused",
            secret_access_key="unused",
        )

    def _filesystem(self):
        return fs.LocalFileSystem()


def _write_day(root, day: str, rows) -> None:
    """rows: list of (ticker, minute_offset, open, close, high, low, volume)."""
    csv = "ticker,volume,open,close,high,low,window_start\n"
    base_ns = pd.Timestamp(f"{day}T14:30:00Z").value
    for ticker, minute_offset, o, c, h, low, v in rows:
        ts_ns = base_ns + minute_offset * 60_000_000_000
        csv += f"{ticker},{v},{o},{c},{h},{low},{ts_ns}\n"
    path = os.path.join(str(root), f"{day}.csv.gz")
    with open(path, "wb") as handle:
        handle.write(gzip.compress(csv.encode()))


def _write_range(root, start_iso: str, n_days: int, symbol="AAPL") -> list:
    """Writes `n_days` consecutive REAL NYSE trading days' flat files
    starting at `start_iso`, via the same pandas_market_calendars source
    tam.marketdata.validate itself uses -- hand-rolling "skip weekends"
    isn't enough (a market holiday like MLK Day is a weekday too), and
    fabricating data for a real holiday is correctly rejected as invalid
    by validate.py, same as it would a real bug. Returns the list of
    `date`s actually written, in order, so a test can target an exact day
    by index instead of guessing calendar dates by hand."""
    import pandas_market_calendars as mcal

    calendar = mcal.get_calendar("NYSE")
    start = date.fromisoformat(start_iso)
    schedule = calendar.schedule(start_date=start, end_date=start + timedelta(days=n_days * 2 + 10))
    trading_days = [ts.date() for ts in schedule.index[:n_days]]

    for offset, day in enumerate(trading_days):
        _write_day(root, day.isoformat(), [(symbol, 0, 1.0 + offset, 1.1 + offset, 1.2 + offset, 0.9 + offset, 100)])
    return trading_days


@pytest.fixture
def flat_files(tmp_path):
    root = tmp_path / "flatfiles"
    root.mkdir()
    return root


@pytest.fixture
def store_root(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    return root


def test_ingest_day_filters_to_universe_validates_and_writes(flat_files, store_root):
    _write_day(flat_files, "2024-01-02", [("AAPL", 0, 1.0, 1.1, 1.2, 0.9, 100), ("ZZZZ", 0, 9, 9, 9, 9, 9)])
    provider = _LocalFlatFileProvider(flat_files)
    store = LocalMinuteBarStore(store_root)
    universe = StaticUniverse(["AAPL"])  # ZZZZ is not a constituent

    outcome = ingest_day(date(2024, 1, 2), provider=provider, store=store, universe=universe)

    assert outcome == "done"
    assert store.exists("AAPL")
    assert not store.exists("ZZZZ")


def test_ingest_day_always_includes_extra_symbols_regardless_of_universe(flat_files, store_root):
    _write_day(flat_files, "2024-01-02", [("SPY", 0, 1.0, 1.1, 1.2, 0.9, 100)])
    provider = _LocalFlatFileProvider(flat_files)
    store = LocalMinuteBarStore(store_root)
    universe = StaticUniverse([])  # SPY is never itself an index constituent

    outcome = ingest_day(date(2024, 1, 2), provider=provider, store=store, universe=universe, extra_symbols=["SPY"])

    assert outcome == "done"
    assert store.exists("SPY")


def test_ingest_day_returns_skipped_no_data_for_a_missing_day(flat_files, store_root):
    provider = _LocalFlatFileProvider(flat_files)
    store = LocalMinuteBarStore(store_root)

    outcome = ingest_day(date(2024, 1, 2), provider=provider, store=store, universe=StaticUniverse(["AAPL"]))

    assert outcome == "skipped_no_data"


def test_ingest_day_returns_skipped_no_data_when_universe_filter_leaves_nothing(flat_files, store_root):
    _write_day(flat_files, "2024-01-02", [("ZZZZ", 0, 1.0, 1.1, 1.2, 0.9, 100)])
    provider = _LocalFlatFileProvider(flat_files)
    store = LocalMinuteBarStore(store_root)

    outcome = ingest_day(date(2024, 1, 2), provider=provider, store=store, universe=StaticUniverse(["AAPL"]))

    assert outcome == "skipped_no_data"
    assert not store.exists("ZZZZ")


def test_ingest_day_raises_on_a_validation_failure_and_writes_nothing(flat_files, store_root):
    _write_day(flat_files, "2024-01-02", [("AAPL", 0, 1.0, 0.5, 0.5, 0.9, 100)])  # high < close/low
    provider = _LocalFlatFileProvider(flat_files)
    store = LocalMinuteBarStore(store_root)

    with pytest.raises(ValueError):
        ingest_day(date(2024, 1, 2), provider=provider, store=store, universe=StaticUniverse(["AAPL"]))

    assert not store.exists("AAPL")


def test_ingest_over_a_range_tallies_processed_and_skipped_days(flat_files, store_root):
    _write_day(flat_files, "2024-01-02", [("AAPL", 0, 1.0, 1.1, 1.2, 0.9, 100)])
    # 2024-01-03 (Wednesday) has no flat file at all -> skipped_no_data
    _write_day(flat_files, "2024-01-04", [("AAPL", 0, 1.0, 1.1, 1.2, 0.9, 100)])
    provider = _LocalFlatFileProvider(flat_files)
    store = LocalMinuteBarStore(store_root)

    result = ingest(
        date(2024, 1, 2), date(2024, 1, 4), provider=provider, store=store, universe=StaticUniverse(["AAPL"])
    )

    assert result == IngestResult(days_processed=2, days_skipped_already_done=0, days_skipped_no_data=1)


def test_ingest_is_resumable_a_second_run_skips_already_done_days(flat_files, store_root):
    _write_day(flat_files, "2024-01-02", [("AAPL", 0, 1.0, 1.1, 1.2, 0.9, 100)])
    provider = _LocalFlatFileProvider(flat_files)
    store = LocalMinuteBarStore(store_root)
    universe = StaticUniverse(["AAPL"])

    first = ingest(date(2024, 1, 2), date(2024, 1, 2), provider=provider, store=store, universe=universe)
    second = ingest(date(2024, 1, 2), date(2024, 1, 2), provider=provider, store=store, universe=universe)

    assert first.days_processed == 1
    assert second.days_processed == 0
    assert second.days_skipped_already_done == 1


def test_ingest_reprocesses_a_day_if_the_vendor_republishes_corrected_data(flat_files, store_root):
    _write_day(flat_files, "2024-01-02", [("AAPL", 0, 1.0, 1.1, 1.2, 0.9, 100)])
    provider = _LocalFlatFileProvider(flat_files)
    store = LocalMinuteBarStore(store_root)
    universe = StaticUniverse(["AAPL"])

    ingest(date(2024, 1, 2), date(2024, 1, 2), provider=provider, store=store, universe=universe)

    # vendor reissues a corrected file for the same day -- force_recheck=True
    # is required to even notice: by default an already-recorded day is
    # skipped WITHOUT re-fetching at all (see test just below this one).
    _write_day(flat_files, "2024-01-02", [("AAPL", 0, 1.0, 1.2, 1.3, 0.9, 150)])
    result = ingest(
        date(2024, 1, 2), date(2024, 1, 2), provider=provider, store=store, universe=universe, force_recheck=True
    )

    assert result.days_processed == 1
    assert list(store.read("AAPL")["close"]) == [1.2]


def test_ingest_default_resume_never_refetches_already_done_days(flat_files, store_root):
    _write_day(flat_files, "2024-01-02", [("AAPL", 0, 1.0, 1.1, 1.2, 0.9, 100)])
    universe = StaticUniverse(["AAPL"])
    store = LocalMinuteBarStore(store_root)

    fetch_log = []

    class _LoggingProvider(_LocalFlatFileProvider):
        def fetch(self, day):
            fetch_log.append(day)
            return super().fetch(day)

    provider = _LoggingProvider(flat_files)
    ingest(date(2024, 1, 2), date(2024, 1, 2), provider=provider, store=store, universe=universe)
    assert fetch_log == [date(2024, 1, 2)]

    fetch_log.clear()
    result = ingest(date(2024, 1, 2), date(2024, 1, 2), provider=provider, store=store, universe=universe)

    # the default (force_recheck=False) resume never calls fetch() again for
    # an already-recorded day -- not just "doesn't re-write," genuinely no
    # network/disk read at all.
    assert fetch_log == []
    assert result.days_skipped_already_done == 1


def test_manifest_persists_across_separate_store_instances_pointed_at_the_same_root(flat_files, store_root):
    _write_day(flat_files, "2024-01-02", [("AAPL", 0, 1.0, 1.1, 1.2, 0.9, 100)])
    provider = _LocalFlatFileProvider(flat_files)
    universe = StaticUniverse(["AAPL"])

    ingest(
        date(2024, 1, 2), date(2024, 1, 2), provider=provider, store=LocalMinuteBarStore(store_root), universe=universe
    )

    fresh_store = LocalMinuteBarStore(store_root)  # a brand new instance, same root
    result = ingest(date(2024, 1, 2), date(2024, 1, 2), provider=provider, store=fresh_store, universe=universe)

    assert result.days_skipped_already_done == 1


class _CountingStore(LocalMinuteBarStore):
    """Counts write() calls -- used to assert ingest() actually batches
    them via flush_every_days instead of calling store.write() once per
    day (which would re-read/re-rewrite a whole year-partition file on
    every call -- see ingest()'s own docstring)."""

    def __init__(self, root):
        super().__init__(root)
        self.write_calls = 0

    def write(self, symbol, df):
        self.write_calls += 1
        super().write(symbol, df)


def test_ingest_batches_store_writes_instead_of_one_per_day(flat_files, store_root):
    trading_days = _write_range(flat_files, "2024-01-02", 12)
    provider = _LocalFlatFileProvider(flat_files)
    store = _CountingStore(store_root)
    universe = StaticUniverse(["AAPL"])

    result = ingest(
        trading_days[0], trading_days[-1], provider=provider, store=store, universe=universe, flush_every_days=5
    )

    assert result.days_processed == 12
    # 12 done-days at flush_every_days=5 -> flushes after the 5th and 10th,
    # plus a trailing flush for the last 2 -- 3 store.write() calls for the
    # one symbol involved, not 12 (any weekend/holiday gaps within the range
    # are skipped_no_data and don't advance the flush counter).
    assert store.write_calls == 3
    assert len(store.read("AAPL")) == 12  # all data still lands correctly despite batching


def test_ingest_resuming_after_a_crash_mid_batch_only_redoes_unflushed_days(flat_files, store_root):
    trading_days = _write_range(flat_files, "2024-01-02", 10)
    crash_day = trading_days[7]  # 3rd day into the SECOND flush_every_days=5 batch
    universe = StaticUniverse(["AAPL"])

    class _CrashingProvider(_LocalFlatFileProvider):
        def fetch(self, day):
            if day == crash_day:
                raise RuntimeError("simulated crash")
            return super().fetch(day)

    store = LocalMinuteBarStore(store_root)

    # max_workers=1 -- forces strictly sequential (calendar-order) processing
    # so which days land in which flush batch is deterministic; ingest()'s
    # concurrency is exercised separately (test_ingest_batches_store_writes_
    # instead_of_one_per_day), where exact per-day ordering doesn't matter.
    with pytest.raises(RuntimeError, match="simulated crash"):
        ingest(
            trading_days[0],
            trading_days[-1],
            provider=_CrashingProvider(flat_files),
            store=store,
            universe=universe,
            flush_every_days=5,
            max_workers=1,
        )

    # The first 5 trading days hit the flush_every_days=5 trigger and were
    # flushed before the crash; the 2 days fetched after that (index 5, 6)
    # were still only pending in memory when crash_day (index 7) raised --
    # lost, same as a real process crash discarding unflushed work, never a
    # partial/corrupt write.
    assert len(store.read("AAPL")) == 5

    # Resuming (same range, a provider that no longer crashes) must skip
    # the already-flushed days and only redo what was actually lost.
    result = ingest(
        trading_days[0],
        trading_days[-1],
        provider=_LocalFlatFileProvider(flat_files),
        store=store,
        universe=universe,
        flush_every_days=5,
        max_workers=1,
    )

    assert result.days_skipped_already_done == 5
    assert result.days_processed == 5
    assert len(store.read("AAPL")) == 10


# -- run_ingest() config wiring --------------------------------------------

_TEST_PROVIDER_NAME = "test_marketdata_fake_provider"
_TEST_STORE_NAME = "test_marketdata_fake_store"
_TEST_UNIVERSE_NAME = "test_marketdata_fake_universe"


@Registry.register(MinuteBarProvider, _TEST_PROVIDER_NAME)
class _RegistryFakeProvider(_LocalFlatFileProvider):
    def __init__(self, root):
        super().__init__(root)


@Registry.register(MinuteBarStore, _TEST_STORE_NAME)
class _RegistryFakeStore(LocalMinuteBarStore):
    pass


@Registry.register(UniverseProvider, _TEST_UNIVERSE_NAME)
class _RegistryFakeUniverse(StaticUniverse):
    pass


def test_run_ingest_wires_up_registry_entries_from_config(tmp_path):
    flat_root = tmp_path / "flat"
    flat_root.mkdir()
    store_root = tmp_path / "store"
    _write_day(flat_root, "2024-01-02", [("AAPL", 0, 1.0, 1.1, 1.2, 0.9, 100)])

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
marketdata:
  provider: {_TEST_PROVIDER_NAME}
  provider_kwargs:
    root: {flat_root}
  store: {_TEST_STORE_NAME}
  store_kwargs:
    root: {store_root}
  universe: {_TEST_UNIVERSE_NAME}
  universe_kwargs:
    tickers: [AAPL]
  start: "2024-01-02"
  end: "2024-01-02"
"""
    )

    result = run_ingest(config_path)

    assert result.days_processed == 1
    assert LocalMinuteBarStore(store_root).exists("AAPL")
