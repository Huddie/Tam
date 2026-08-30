"""Orchestrates a resumable, idempotent backfill: for each day in a range,
fetch one vendor's full-market flat file (MinuteBarProvider), filter it down
to SPY + that day's point-in-time S&P 500 constituents (reusing
tam.basket.universe.UniverseProvider as-is -- no new universe abstraction),
validate (tam.marketdata.validate), and UPSERT into a MinuteBarStore.

Idempotent/resumable: a small JSON manifest at `<store root>/_manifest.json`
records which days have already been ingested, keyed by a content hash of
that day's fetched frame. By default, a day already in the manifest is
skipped WITHOUT even fetching it again -- a genuinely fast resume, not just
a fast re-validate/re-write. Pass `force_recheck=True` to re-fetch and
compare hashes for already-done days too (detects a vendor republishing a
CORRECTED file for a day already ingested) -- an explicit opt-in rather than
the default, since checking for that on every single resume would mean
re-downloading the ENTIRE already-completed range every time, which for a
multi-year backfill is tens of GB of pointless repeat downloads for a
genuinely rare event. Run with `force_recheck=True` occasionally (e.g. a
periodic maintenance pass over the most recent week) instead of on every
resume.

A validation failure raises immediately rather than being caught and
tallied -- "caught loudly, before ever starting" (to borrow the convention
tam.strategy.mlx_lora_client's own tests already establish) beats silently
skipping a bad day or half-writing it. Re-running ingest() after fixing
whatever caused the failure resumes from the manifest; already-good days
aren't redone.

Progress is reported through tam.status.report, the same hook LoRA
fine-tuning already uses to drive a CLI progress bar -- ingest() itself
knows nothing about how (or whether) that's displayed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from .. import status
from ..basket.universe import UniverseProvider
from ..config import Config
from ..registry import Registry
from .providers import MinuteBarProvider
from .schema import SYMBOL
from .store import MinuteBarStore
from .validate import validate_day


def _calendar_days(start: date, end: date) -> list[date]:
    """Every calendar day in [start, end] -- weekends/holidays fall out
    naturally later (the provider returns an empty frame for them, and
    ingest() just moves on); no separate trading-calendar dependency is
    needed here just to decide which days to ATTEMPT fetching."""
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _content_hash(frame: pd.DataFrame) -> str:
    """A cheap content fingerprint for one day's already-fetched (still
    unfiltered) frame -- not a hash of the vendor's raw bytes (the provider
    abstraction doesn't expose those), but good enough to detect "the vendor
    republished different data for a day we already ingested" without
    re-uploading every unchanged day on every resumed run."""
    return hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes()).hexdigest()


class _Manifest:
    """`{day_iso: content_hash}`, persisted via the store's own
    read_manifest_bytes()/write_manifest_bytes() -- NOT written with the
    atomic tempfile-rename dance tam.backtest.harness's own checkpoint uses;
    a corrupt/partial manifest is harmless here (worst case a day gets
    redundantly re-ingested, which MinuteBarStore.write's UPSERT semantics
    make safe, not a correctness bug), so the extra mechanism isn't worth
    it.

    `record()` only updates the in-memory copy -- call `flush()` to persist.
    Kept separate so a caller doing many days in a row (see ingest()'s
    `flush_every_days` batching) can record every day immediately (cheap,
    in-memory) while only writing the file itself every so often, instead of
    rewriting it after literally every single day."""

    def __init__(self, store: MinuteBarStore):
        self._store = store
        self._data = self._read()

    def _read(self) -> dict:
        raw = self._store.read_manifest_bytes()
        if raw is None:
            return {}
        return json.loads(raw)

    def hash_for(self, day: date) -> str | None:
        return self._data.get(day.isoformat())

    def record(self, day: date, content_hash: str) -> None:
        self._data[day.isoformat()] = content_hash

    def flush(self) -> None:
        self._store.write_manifest_bytes(json.dumps(self._data, indent=2).encode("utf-8"))


@dataclass
class IngestResult:
    days_processed: int = 0
    days_skipped_already_done: int = 0
    days_skipped_no_data: int = 0


def _prepare_day(
    day: date,
    *,
    provider: MinuteBarProvider,
    universe: UniverseProvider,
    extra_symbols: Sequence[str],
    manifest: _Manifest | None,
    force_recheck: bool = False,
):
    """Fetch, skip-check, filter, and validate one day -- shared by
    ingest_day() (self-contained, one day at a time) and ingest()'s batched,
    concurrent loop (which defers the actual store write). Returns
    (outcome, filtered_df_or_None, content_hash_or_None); outcome is
    "done" / "skipped_already_done" / "skipped_no_data". Raises ValueError
    on a validation failure -- see module docstring.

    Checks the manifest BEFORE fetching when `force_recheck` is False (the
    default): a day already recorded is skipped with NO network call at
    all, not just no re-write. `force_recheck=True` fetches regardless and
    compares the hash instead, to detect a vendor's corrected re-publish --
    see module docstring for why that's opt-in, not automatic."""
    if manifest is not None and not force_recheck and manifest.hash_for(day) is not None:
        return "skipped_already_done", None, None

    raw = provider.fetch(day)
    if raw.empty:
        return "skipped_no_data", None, None

    content_hash = _content_hash(raw)
    if manifest is not None and manifest.hash_for(day) == content_hash:
        return "skipped_already_done", None, None

    allowed = set(extra_symbols) | set(universe.constituents(day))
    filtered = raw[raw[SYMBOL].isin(allowed)]
    if filtered.empty:
        return "skipped_no_data", None, None

    validate_day(filtered, day).raise_if_invalid()
    return "done", filtered, content_hash


def ingest_day(
    day: date,
    *,
    provider: MinuteBarProvider,
    store: MinuteBarStore,
    universe: UniverseProvider,
    extra_symbols: Sequence[str] = (),
    manifest: _Manifest | None = None,
    force_recheck: bool = False,
) -> str:
    """Fetches, filters, validates, and UPSERTs one day -- a self-contained
    unit of work (writes the store and persists the manifest immediately,
    unlike ingest()'s own batched loop below -- see that function's
    docstring for why it doesn't call this directly). Returns "done" /
    "skipped_already_done" / "skipped_no_data" (a holiday/weekend, or a day
    where the universe filter left nothing). Raises ValueError on a
    validation failure -- see module docstring."""
    outcome, filtered, content_hash = _prepare_day(
        day,
        provider=provider,
        universe=universe,
        extra_symbols=extra_symbols,
        manifest=manifest,
        force_recheck=force_recheck,
    )
    if outcome != "done":
        return outcome

    for symbol, group in filtered.groupby(SYMBOL):
        store.write(symbol, group)

    if manifest is not None:
        manifest.record(day, content_hash)
        manifest.flush()
    return "done"


def ingest(
    start: date,
    end: date,
    *,
    provider: MinuteBarProvider,
    store: MinuteBarStore,
    universe: UniverseProvider,
    extra_symbols: Sequence[str] = ("SPY",),
    flush_every_days: int = 20,
    max_workers: int = 8,
    flush_workers: int = 16,
    force_recheck: bool = False,
) -> IngestResult:
    """Backfills every calendar day in [start, end]. `extra_symbols`
    (default just SPY) are always included regardless of index membership:
    SPY itself is never an S&P 500 CONSTITUENT (it's the fund tracking the
    index, not a member of it), so it would never survive a plain
    UniverseProvider filter on its own.

    Fetches up to `max_workers` days CONCURRENTLY (network I/O-bound -- a
    thread pool, not a process pool, matching tam.data.repository.
    DataRepository.ingest()'s own reasoning for its own concurrent fetch)
    rather than one day at a time; for a multi-year backfill (thousands of
    trading days) this is the difference between hours and a fraction of
    that. `_FlatFileS3Provider.fetch()` builds a fresh S3FileSystem per call
    (no shared mutable client state to race on), and store writes/manifest
    updates always happen back on the calling thread as each day's result
    comes back -- as_completed() yields whichever day's FETCH finished
    first, not calendar order, which is fine: every downstream step (skip-
    check, filtering, validation, batching) is per-day-independent, and
    MinuteBarStore.write() sorts by timestamp on write regardless of the
    order rows were appended in.

    `flush_workers` is DELIBERATELY a separate, higher-by-default knob than
    `max_workers` -- confirmed in production to matter, not just a
    theoretical distinction: one flush writes roughly the ENTIRE point-in-
    time universe (~500-800 symbols, since nearly all of them trade every
    day), while `max_workers` only ever needs to cover fetching one day at
    a time. At `max_workers`-many flush workers, one flush of the full
    universe took over a minute (500-800 small, independent, cheap R2 PUTs,
    bottlenecked on worker count alone), while fetching -- unblocked in its
    own threads the whole time -- kept racing ahead by YEARS, making it look
    like the backfill had stalled or lost data when it had actually just
    fallen far behind on writes specifically. boto3 clients are documented
    thread-safe and each write hits a different key, so there's no
    correctness reason to keep this tied to fetch concurrency.

    Deliberately does NOT call ingest_day() per day here, even though that
    would be simpler: MinuteBarStore.write() reads, merges, and rewrites a
    symbol's ENTIRE year-partition file on every call -- writing once per
    calendar day would mean re-reading and re-rewriting an ever-growing file
    up to ~252 times per symbol per year (quadratic in days-per-year), which
    makes a multi-year backfill impractically slow. Instead, fetched/
    filtered/validated days are buffered per symbol and only flushed (one
    store.write() per symbol covering the whole buffered batch, plus one
    manifest write) every `flush_every_days` days, and once more at the end
    for any trailing partial batch -- bounding both the rewrite-amplification
    cost AND how much already-fetched work a crash mid-backfill could lose,
    without needing to hold years of data in memory at once."""
    manifest = _Manifest(store)
    result = IngestResult()

    pending_by_symbol: dict[str, list[pd.DataFrame]] = {}
    pending_count = 0

    def _write_one(item) -> None:
        symbol, frames = item
        store.write(symbol, pd.concat(frames))

    def _flush() -> None:
        nonlocal pending_count
        if pending_by_symbol:
            # Each symbol's store.write() is its own full R2 round-trip
            # (read the year file, merge, rewrite) -- hundreds of symbols
            # done one at a time here would make the WRITE side the
            # bottleneck even though fetching is already concurrent above;
            # different symbols write to different paths, so there's
            # nothing to race on running these concurrently too.
            with ThreadPoolExecutor(max_workers=flush_workers) as write_pool:
                list(write_pool.map(_write_one, pending_by_symbol.items()))
        pending_by_symbol.clear()
        manifest.flush()
        pending_count = 0

    days = _calendar_days(start, end)
    total = len(days)
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _prepare_day,
                day,
                provider=provider,
                universe=universe,
                extra_symbols=extra_symbols,
                manifest=manifest,
                force_recheck=force_recheck,
            ): day
            for day in days
        }
        for future in as_completed(futures):
            day = futures[future]
            completed += 1
            status.report(f"ingesting minute bars: {day}", completed, total)
            outcome, filtered, content_hash = future.result()

            if outcome == "done":
                for symbol, group in filtered.groupby(SYMBOL):
                    pending_by_symbol.setdefault(symbol, []).append(group)
                manifest.record(day, content_hash)
                pending_count += 1
                result.days_processed += 1
            elif outcome == "skipped_already_done":
                result.days_skipped_already_done += 1
            else:
                result.days_skipped_no_data += 1

            if pending_count >= flush_every_days:
                _flush()

    _flush()
    return result


class MarketDataSettings:
    """The `marketdata:` config section -- declarative Registry names +
    constructor kwargs for the provider/store/universe, plus the backfill's
    own date range. Mirrors tam.data.export's DataSettings/ExportSettings
    shape: a plain attribute container tam.config.DotDict.__call__
    populates from YAML, nothing more."""

    provider: str
    provider_kwargs: dict | None = None
    store: str
    store_kwargs: dict | None = None
    universe: str = "static"
    universe_kwargs: dict | None = None
    extra_symbols: list[str] | None = None
    flush_every_days: int | None = None
    max_workers: int | None = None
    flush_workers: int | None = None
    force_recheck: bool | None = None
    start: str
    end: str


def _plain_kwargs(value: Any) -> dict[str, Any]:
    """A DotDict section (or None, or an already-plain dict) -> a plain
    dict suitable for **kwargs -- Registry.create()'s constructors don't
    know or care about tam.config.DotDict."""
    if value is None:
        return {}
    return value.to_dict() if hasattr(value, "to_dict") else dict(value)


def run_ingest(config_path: str | Path) -> IngestResult:
    """Config-driven counterpart to ingest() -- reads the `marketdata:`
    section from `config_path` (see MarketDataSettings) and calls ingest()
    with it. See examples/ingest_minute_bars.py for the CLI wrapper, and
    MARKETDATA.md for a full config example (local dev and R2 production)."""
    cfg = Config(Path(config_path))
    settings = cfg.marketdata(MarketDataSettings)

    provider = Registry.create(MinuteBarProvider, settings.provider, **_plain_kwargs(settings.provider_kwargs))
    store = Registry.create(MinuteBarStore, settings.store, **_plain_kwargs(settings.store_kwargs))
    universe = Registry.create(UniverseProvider, settings.universe, **_plain_kwargs(settings.universe_kwargs))

    ingest_kwargs: dict[str, Any] = {}
    if settings.flush_every_days is not None:
        ingest_kwargs["flush_every_days"] = settings.flush_every_days
    if settings.max_workers is not None:
        ingest_kwargs["max_workers"] = settings.max_workers
    if settings.flush_workers is not None:
        ingest_kwargs["flush_workers"] = settings.flush_workers
    if settings.force_recheck is not None:
        ingest_kwargs["force_recheck"] = settings.force_recheck

    return ingest(
        date.fromisoformat(settings.start),
        date.fromisoformat(settings.end),
        provider=provider,
        store=store,
        universe=universe,
        extra_symbols=settings.extra_symbols or ["SPY"],
        **ingest_kwargs,
    )


@dataclass
class CoverageReport:
    expected: list[date]
    ingested: list[date]
    missing: list[date]

    @property
    def is_complete(self) -> bool:
        return not self.missing


def coverage_report(store: MinuteBarStore, start: date, end: date, calendar: str = "NYSE") -> CoverageReport:
    """Which expected NYSE trading days in [start, end] are actually
    recorded as ingested in `store`'s manifest.

    Answers "are all days properly ingested" WITHOUT needing day-partitioned
    storage: the resumability manifest ingest() already writes tracks
    completion at day granularity regardless of how the underlying Parquet
    itself is partitioned (year files here) -- this just reads that back and
    compares it against the real NYSE trading calendar (the same source
    tam.marketdata.validate uses) instead of assuming every weekday should
    have data. `missing` is exactly the set of days re-running ingest() over
    this same range will pick up -- its own resumability means already-good
    days aren't touched, only these."""
    import pandas_market_calendars as mcal

    manifest = _Manifest(store)
    schedule = mcal.get_calendar(calendar).schedule(start_date=start, end_date=end)
    expected = [ts.date() for ts in schedule.index]
    ingested = [day for day in expected if manifest.hash_for(day) is not None]
    missing = [day for day in expected if manifest.hash_for(day) is None]
    return CoverageReport(expected=expected, ingested=ingested, missing=missing)


def run_coverage_report(config_path: str | Path) -> CoverageReport:
    """Config-driven counterpart to coverage_report() -- reads the same
    `marketdata:` section run_ingest() does (only `store`/`store_kwargs`/
    `start`/`end` matter here; `provider`/`universe` are ignored), so this
    can point at the exact same config file a backfill used. See
    examples/check_ingest_coverage.py for the CLI wrapper."""
    cfg = Config(Path(config_path))
    settings = cfg.marketdata(MarketDataSettings)
    store = Registry.create(MinuteBarStore, settings.store, **_plain_kwargs(settings.store_kwargs))
    return coverage_report(store, date.fromisoformat(settings.start), date.fromisoformat(settings.end))
