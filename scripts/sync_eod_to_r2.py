"""One-time repair: pushes any symbol already on local disk (--root) but
missing from R2 straight into R2 -- no yfinance re-fetch needed, since the
data is already correct locally.

Why this is needed: scripts/backfill_sp500_eod.py's very first run wrote
data LOCAL-ONLY (before R2DataStore existed). DataRepository.ingest()'s gap
detection only ever looks at whichever store it's given -- when wrapped in
a MultiDataStore, that's the PRIMARY (local) store -- so once local disk
already fully covers a symbol's date range, a plain re-run of the ingest
script never touches that symbol again, and it never gets the chance to
fan its write out to the newly-added R2 store either. This is why R2 ended
up missing roughly the first alphabetical batch of S&P 500 tickers (A,
AAPL, ABBV, ...) that got ingested before R2 support existed, while every
symbol ingested after that point (plus all the indices) is fine.

Usage:
    uv run python scripts/sync_eod_to_r2.py
    uv run python scripts/sync_eod_to_r2.py --root data/eod
    uv run python scripts/sync_eod_to_r2.py --symbol AAPL --symbol MSFT

Writing to R2DataStore also writes a completeness sidecar automatically
(the same _upsert_partition hook every other EOD write path uses) -- so
this fixes both the missing data AND the missing completeness sidecar for
R2 in one pass; no separate scripts/backfill_eod_completeness.py run needed
afterward for these symbols.

Safe to re-run: R2DataStore.write() is an UPSERT, and the source data for
an already-synced symbol is identical, so re-running is a harmless no-op.
Runs concurrently via a thread pool -- each unit of work is one local read
plus one R2 write, I/O-bound like every other backfill script here.
"""
from __future__ import annotations

import os
import time
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple

from tam.data.storage import DataStore, ParquetStore
from tam.registry import Registry

DEFAULT_WORKERS = 8


def _local_symbols(root: str) -> List[str]:
    return sorted(
        name
        for name in os.listdir(root)
        if os.path.isdir(os.path.join(root, name))
        and any(f.endswith(".parquet") for f in os.listdir(os.path.join(root, name)))
    )


def _sync_one(local_store: ParquetStore, r2_store: DataStore, symbol: str) -> bool:
    """Returns True if anything was written (the symbol had local data)."""
    df = local_store.read(symbol)
    if df.empty:
        return False
    r2_store.write(symbol, df)
    return True


def main() -> None:
    parser = ArgumentParser(description=__doc__, formatter_class=RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="data/eod", help="Local DataStore root to read from (default: data/eod)")
    parser.add_argument(
        "--symbol", action="append", dest="symbols", help="Only sync this symbol (repeatable) -- default: every symbol dir under --root"
    )
    parser.add_argument(
        "--workers", type=int, default=DEFAULT_WORKERS, help=f"Concurrent worker threads (default: {DEFAULT_WORKERS})"
    )
    args = parser.parse_args()

    symbols = args.symbols or _local_symbols(args.root)
    print(f"{len(symbols)} local symbol(s) under {args.root} to sync to R2.")

    local_store = ParquetStore(args.root)
    r2_store: DataStore = Registry.create(DataStore, "r2_parquet")

    started = time.monotonic()
    synced = 0
    empty = 0
    failed: List[Tuple[str, Exception]] = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_sync_one, local_store, r2_store, symbol): symbol for symbol in symbols}
        for i, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            try:
                wrote = future.result()
            except Exception as exc:  # noqa: BLE001 -- report and keep going; one bad symbol shouldn't abort the rest
                failed.append((symbol, exc))
                print(f"  [{i}/{len(symbols)}] {symbol}: FAILED -- {exc}")
                continue
            if wrote:
                synced += 1
            else:
                empty += 1
            if i % 25 == 0 or i == len(symbols):
                print(f"  [{i}/{len(symbols)}] ... ({time.monotonic() - started:.0f}s elapsed)")

    print(f"Done in {time.monotonic() - started:.0f}s. Synced {synced}, empty {empty}, {len(failed)} failed.")
    for symbol, exc in failed:
        print(f"  FAILED {symbol}: {exc}")


if __name__ == "__main__":
    main()
