"""Repository: combines a DataProvider (fetch) and a DataStore (persist) into ingest/query."""
from __future__ import annotations

import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd

from .history import SymbolHistory
from .providers import DataProvider
from .storage import DataStore

if TYPE_CHECKING:
    from .writer import RepoWriter


class DataRepository:
    """Ingests fresh data from a provider into a store, and serves queries back out of it."""

    def __init__(self, provider: DataProvider, store: DataStore):
        self._provider = provider
        self._store = store
        self._cache: Dict[str, SymbolHistory] = {}
        # (symbol, gap_start, gap_end) tuples already confirmed empty THIS
        # session -- e.g. requesting today's bar before it's posted fails
        # for every symbol, every ingest() call, until tomorrow actually
        # arrives; re-running the same cell/script in the meantime shouldn't
        # re-hit the network (and re-warn) for the exact same known-empty
        # range. In-memory only (not persisted to the store) -- resets on a
        # new process/kernel, so a transient failure never gets "stuck"
        # looking permanently empty across sessions.
        self._known_empty: Set[Tuple[str, date, date]] = set()

    def history(self, symbol: str) -> SymbolHistory:
        """A symbol's full history, read from the store at most once per repository
        instance -- callers doing repeated point-in-time lookups (price marks, a
        strategy's lookback window) should use this instead of re-querying, since
        `DataStore.read` re-reads and re-concatenates every partition file on disk
        on every call."""
        if symbol not in self._cache:
            self._cache[symbol] = SymbolHistory(self._store.read(symbol))
        return self._cache[symbol]

    def ingest(self, symbols: Iterable[str], start: date, end: date, max_workers: int = 8) -> None:
        """Fetches every symbol's missing [start, end] sub-range CONCURRENTLY
        (network I/O-bound -- a thread pool, not a process pool) via
        `max_workers` worker threads, rather than one network round-trip at
        a time; for a few hundred tickers this is the difference between
        minutes and seconds. Store writes/cache invalidation happen back on
        the calling thread as each fetch completes, never inside a worker
        thread, so there's no risk of two threads racing on the same
        symbol's store partition or `self._cache` entry."""
        tasks = []
        for symbol in symbols:
            existing = self._store.read(symbol) if self._store.exists(symbol) else None
            for gap_start, gap_end in self._missing_ranges(existing, start, end):
                if (symbol, gap_start, gap_end) not in self._known_empty:
                    tasks.append((symbol, gap_start, gap_end))
        if not tasks:
            return

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._provider.fetch_eod, symbol, gap_start, gap_end): (symbol, gap_start, gap_end)
                for symbol, gap_start, gap_end in tasks
            }
            for future in as_completed(futures):
                symbol, gap_start, gap_end = futures[future]
                fresh = future.result()
                if not fresh.empty:
                    self._store.write(symbol, fresh)
                    self._cache.pop(symbol, None)
                else:
                    self._known_empty.add((symbol, gap_start, gap_end))
                    warnings.warn(
                        f"no data returned for {symbol} in [{gap_start}, {gap_end}] -- "
                        "leaving this range uncached; a strategy that later trades this "
                        "symbol on one of these dates will fail with a clear LookupError "
                        "rather than silently getting stale/missing prices",
                        stacklevel=2,
                    )

    @staticmethod
    def _missing_ranges(
        existing: Optional[pd.DataFrame], start: date, end: date
    ) -> List[Tuple[date, date]]:
        """Date sub-ranges within [start, end] not already covered by `existing`."""
        if existing is None or existing.empty:
            return [(start, end)]

        covered_start = existing.index.min().date()
        covered_end = existing.index.max().date()

        gaps: List[Tuple[date, date]] = []
        if start < covered_start:
            gaps.append((start, min(end, covered_start - timedelta(days=1))))
        if end > covered_end:
            gaps.append((max(start, covered_end + timedelta(days=1)), end))
        return gaps

    def query(
        self,
        symbol: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> pd.DataFrame:
        df = self.history(symbol).frame
        if start is not None:
            df = df[df.index >= pd.Timestamp(start)]
        if end is not None:
            df = df[df.index <= pd.Timestamp(end)]
        return df.copy()

    def write(self, writer: "RepoWriter", symbols: Iterable[str]) -> Any:
        """Hand every already-ingested `symbols`' full history to `writer` --
        `Registry.get(RepoWriter, "csv"/"parquet")` for a flat-file dump, or
        your own RepoWriter for anything that isn't a file at all (S3, a
        database, an in-memory object). Return value is whatever that writer
        itself returns -- see its own docs. Does NOT ingest first; call
        ingest(symbols, start, end) beforehand same as you would before query()."""
        return writer.write({symbol: self.query(symbol) for symbol in symbols})
