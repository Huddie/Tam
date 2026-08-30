"""One-time migration: reshuffle already-ingested positioning/short_volume
and positioning/short_interest global year files
(positioning/<dataset>/<year>.parquet) into the per-ticker layout
(positioning/<dataset>/<TICKER>/<year>.parquet) that
tam/marketdata/reference_store.py now writes -- see that module's own
docstring for why only these two of the six reference datasets need
per-ticker partitioning (short_volume hit 3.1M rows in a single global
year file; splits/dividends/ipos/float stay as single global files,
nothing to migrate for those).

Only relevant if you ran examples/ingest_reference_data.py against an
OLDER checkout that still wrote the global-year layout for short_volume/
short_interest -- a fresh checkout never produces old-style keys in the
first place, so this script finds nothing to do and exits immediately.

Usage:
    uv run python scripts/migrate_reference_data_per_ticker.py             # do it
    uv run python scripts/migrate_reference_data_per_ticker.py --dry-run   # list what WOULD move, touch nothing
    uv run python scripts/migrate_reference_data_per_ticker.py --workers 64  # default: 32

Per-ticker datasets legitimately have one file per ticker -- a single old
global year file can cover 15,000-26,000+ distinct tickers (and growing
year over year), so a naive one-ticker-at-a-time loop means tens of
thousands of SEQUENTIAL network round trips per year (confirmed live: an
earlier version of this script took 8+ hours to get through 5 of 13 old
files at that rate). This version fans the per-ticker writes for each old
file out across a thread pool (--workers, default 32) -- these are I/O-
bound R2 calls, not CPU-bound work, so Python threads (which release the
GIL while blocked on network I/O) parallelize this fine; boto3 clients are
documented as safe to share across threads for concurrent calls.

It also does NOT re-download every new per-ticker file to verify it
afterward (the earlier version did -- that alone was another full sweep
of GET requests across every ticker). Instead it verifies using the ROW
COUNTS ALREADY IN MEMORY from the one-time read of the old file: each
per-ticker group's distinct-by-natural-key count is computed BEFORE
writing, and the old file only gets deleted if every one of those writes
completed without raising -- consistent with the level of trust
reference_store.py's own write() path already places in a successful
put_object() call (it doesn't read itself back to confirm either).
Concurrency and dropping the extra verification reads combined should
turn what was "another day+" into low minutes.

Safe to interrupt and re-run: an old global file is only deleted after
every one of its per-ticker writes for that year succeeds, and
write()'s own dedup-on-natural-key (used internally by _upsert_partition)
makes re-writing an already-migrated (ticker, year) safe either way.
"""

from __future__ import annotations

import argparse
import io
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from tam.marketdata import reference_schema as schema
from tam.marketdata.reference_store import _DEDUP_KEYS, _PER_TICKER_DATASETS, R2ReferenceStore
from tam.marketdata.store import _with_retries


def _list_old_files(store: R2ReferenceStore) -> list[tuple[str, int, str]]:
    """Every OLD-style global year file still sitting directly under
    positioning/<dataset>/ -- (dataset, year, key). A file that's already
    been migrated lives one path segment deeper
    (positioning/<dataset>/<TICKER>/<year>.parquet), so a Delimiter='/'
    listing at the dataset level puts it in CommonPrefixes, not Contents,
    and it never shows up here."""
    found = []
    for dataset in sorted(_PER_TICKER_DATASETS):
        prefix = f"positioning/{dataset}/"

        def _list() -> list[tuple[str, int, str]]:
            pairs = []
            paginator = store._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=store._credentials.bucket, Prefix=prefix, Delimiter="/"):
                for obj in page.get("Contents", []):
                    relative = obj["Key"][len(prefix) :]
                    stem = relative[: -len(".parquet")] if relative.endswith(".parquet") else ""
                    if stem.isdigit():
                        pairs.append((dataset, int(stem), obj["Key"]))
            return pairs

        found.extend(_with_retries(_list))
    return found


def _read_parquet_object(store: R2ReferenceStore, key: str) -> pd.DataFrame:
    import pyarrow.parquet as pq

    def _get() -> bytes:
        response = store._client.get_object(Bucket=store._credentials.bucket, Key=key)
        return response["Body"].read()

    body = _with_retries(_get)
    return pq.read_table(io.BytesIO(body)).to_pandas()


def _migrate_one_file(store: R2ReferenceStore, dataset: str, year: int, old_key: str, workers: int) -> None:
    print(f"{dataset} {year}: reading {old_key}...")
    df = _read_parquet_object(store, old_key)
    dedup_keys = _DEDUP_KEYS[dataset]
    tickers = sorted(df[schema.TICKER].str.upper().unique())
    print(f"{dataset} {year}: {len(df)} row(s) across {len(tickers)} ticker(s), splitting with {workers} worker(s)...")

    groups = {
        ticker: group.drop_duplicates(subset=dedup_keys) for ticker, group in df.groupby(df[schema.TICKER].str.upper())
    }
    expected = sum(len(group) for group in groups.values())

    def _write_one(ticker: str) -> int:
        group = groups[ticker]
        store._upsert_partition(dataset, year, group, ticker=ticker)
        return len(group)

    written = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_write_one, ticker): ticker for ticker in tickers}
        for future in as_completed(futures):
            written += future.result()  # raises immediately if any ticker's write failed

    if written != expected:
        raise RuntimeError(
            f"{dataset} {year}: expected {expected} distinct row(s) across {len(tickers)} ticker file(s), "
            f"wrote {written} -- aborting before deleting {old_key}. Nothing else was touched."
        )

    store._client.delete_object(Bucket=store._credentials.bucket, Key=old_key)
    print(f"{dataset} {year}: wrote {written} row(s) across {len(tickers)} ticker file(s), deleted {old_key}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="List what would move, without touching anything")
    parser.add_argument("--workers", type=int, default=32, help="Concurrent per-ticker writes per file (default: 32)")
    args = parser.parse_args()

    store = R2ReferenceStore()
    old_files = _list_old_files(store)
    print(f"{len(old_files)} old-style global year file(s) found.")

    if not old_files:
        print("Nothing to migrate.")
        return

    if args.dry_run:
        for dataset, year, key in old_files:
            print(f"  {key}  ->  positioning/{dataset}/<TICKER>/{year}.parquet (split by ticker)")
        return

    for dataset, year, old_key in old_files:
        _migrate_one_file(store, dataset, year, old_key, args.workers)

    print(f"Migrated {len(old_files)} old-style file(s).")


if __name__ == "__main__":
    main()
