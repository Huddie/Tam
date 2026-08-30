"""Orchestrates ingestion of the six Massive/Polygon reference datasets --
splits, dividends, IPOs, short volume, short interest, float. Simpler than
tam.marketdata.ingest's minute-bar orchestration on purpose: no universe
filtering (every dataset here is a global feed, not per-symbol), no
concurrency (each dataset is one paginated fetch, not thousands of daily
files), no content-hash resumability (a cursor is enough, since these are
either genuinely append-only or have no incremental concept at all).

Four datasets are append-only and incremental: a small JSON manifest per
GROUP (`corporate_actions`/`positioning`, persisted via the store's own
read_manifest_bytes()/write_manifest_bytes(group, ...)) records each
dataset's own cursor (the newest date column value seen so far) --
re-running ingest_reference_data() only fetches rows newer than that,
same "record()-then-flush()" shape as tam.marketdata.ingest._Manifest,
just cursor-keyed instead of day+hash-keyed.

Two datasets (ipos, float) have no history/cursor concept at all -- every
run re-fetches the full current table; see
tam.marketdata.reference_provider's fetch_ipos()/fetch_float() docstrings
for why (mutable records / no date-range param on that endpoint at all).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .reference_provider import MassiveReferenceProvider
from .reference_store import _DATASET_GROUPS, _DATE_COLUMNS, ReferenceStore

_MANIFEST_GROUPS = ("corporate_actions", "positioning")

_APPEND_ONLY = ["splits", "dividends", "short_volume", "short_interest"]
_SNAPSHOT = ["ipos", "float"]

_FETCH_METHODS = {
    "splits": "fetch_splits",
    "dividends": "fetch_dividends",
    "short_volume": "fetch_short_volume",
    "short_interest": "fetch_short_interest",
    "ipos": "fetch_ipos",
    "float": "fetch_float",
}


class Manifest:
    """`{"<dataset>_cursor": "<iso-date>"}` for the append-only datasets in
    ONE group ("corporate_actions" | "positioning"), persisted via the
    store's read_manifest_bytes(group)/write_manifest_bytes(group, ...) --
    same record()-then-flush() shape as tam.marketdata.ingest._Manifest,
    just cursor-keyed instead of day+hash-keyed (see reference_ingest.py's
    own module docstring for why these datasets need a different
    incremental unit than minute bars' day-level one). A corrupt/missing
    manifest is harmless (worst case a dataset gets redundantly re-fetched
    from the start, which write()'s own dedup-on-merge makes safe, not a
    correctness bug) -- same tolerance as the minute-bar manifest."""

    def __init__(self, store: ReferenceStore, group: str):
        self._store = store
        self._group = group
        self._data = self._read()

    def _read(self) -> dict:
        raw = self._store.read_manifest_bytes(self._group)
        if raw is None:
            return {}
        return json.loads(raw)

    def cursor_for(self, dataset: str) -> Optional[str]:
        return self._data.get(f"{dataset}_cursor")

    def record(self, dataset: str, cursor: str) -> None:
        self._data[f"{dataset}_cursor"] = cursor

    def flush(self) -> None:
        self._store.write_manifest_bytes(self._group, json.dumps(self._data, indent=2).encode("utf-8"))


@dataclass
class DatasetResult:
    dataset: str
    rows_fetched: int


def ingest_reference_data(
    provider: MassiveReferenceProvider, store: ReferenceStore, *, log: Optional[Callable[[str], None]] = None
) -> List[DatasetResult]:
    """Runs all six datasets once: the four append-only ones incrementally
    (only rows newer than each one's own stored cursor), the two
    full-refresh ones (ipos, float) wholesale every time. Safe to call
    repeatedly/daily -- an append-only dataset with nothing new since the
    last run just fetches an empty page and moves on (its cursor is left
    untouched, not reset); a full-refresh dataset always re-fetches, which
    is the whole point of it being a snapshot dataset in the first place.

    `log`, if given, gets a start/finish line per dataset (six total)
    PLUS one line per page for splits/dividends specifically -- those two
    go through MassiveReferenceProvider's own manual pagination loop
    (see its _paginate_raw() docstring) and can genuinely run to hundreds
    of pages, unlike the other four, which paginate internally inside the
    `massive` SDK with no per-page hook to report through."""
    log = log or (lambda _message: None)
    results: List[DatasetResult] = []
    manifests: Dict[str, Manifest] = {group: Manifest(store, group) for group in _MANIFEST_GROUPS}

    for dataset in _APPEND_ONLY:
        manifest = manifests[_DATASET_GROUPS[dataset]]
        cursor = manifest.cursor_for(dataset)
        log(f"{dataset}: fetching (since {cursor or 'the beginning'})...")
        fetch = getattr(provider, _FETCH_METHODS[dataset])
        df = fetch(since=cursor, log=log) if dataset in ("splits", "dividends") else fetch(since=cursor)
        log(f"{dataset}: {len(df)} row(s) fetched")
        if not df.empty:
            store.write(dataset, df)
            date_col = _DATE_COLUMNS[dataset]
            new_cursor = str(df[date_col].max())
            manifest.record(dataset, new_cursor)
            manifest.flush()
        results.append(DatasetResult(dataset=dataset, rows_fetched=len(df)))

    for dataset in _SNAPSHOT:
        log(f"{dataset}: fetching (full refresh)...")
        fetch = getattr(provider, _FETCH_METHODS[dataset])
        df = fetch()
        log(f"{dataset}: {len(df)} row(s) fetched")
        store.write(dataset, df)
        results.append(DatasetResult(dataset=dataset, rows_fetched=len(df)))

    return results
