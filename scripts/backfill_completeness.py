"""One-time (or re-run-safe) backfill: computes and writes a completeness
sidecar (see tam.marketdata.completeness) for every symbol-year ALREADY in
the R2 bucket -- new ingests get this automatically now (see
MinuteBarStore._upsert_partition), so this script only exists to catch up
data that was written before that change landed.

Usage:
    uv run python scripts/backfill_completeness.py
    uv run python scripts/backfill_completeness.py --symbol AAPL --symbol MSFT
    uv run python scripts/backfill_completeness.py --force      # recompute even where a sidecar already exists
    uv run python scripts/backfill_completeness.py --workers 16  # default: 8

Runs symbol-year backfills concurrently via a thread pool -- each unit of
work is one R2 GET (the year's parquet) plus at most one PUT (the
sidecar), i.e. I/O-bound waiting on the network, exactly what threads (not
multiprocessing) are for. A single boto3 client is safe to share across
threads this way -- it manages its own connection pooling/locking
internally, the same assumption tam.marketdata.ingest's own batched writes
already rely on.

Reads R2 credentials the usual way (tam.marketdata.credentials.
resolve_r2_credentials: kwarg -> env var (directly, or via a .env file) ->
Colab secret -> saved file).

Idempotent by default: without --force, a symbol-year that already has a
sidecar is skipped, so a re-run after an interrupted backfill only does the
remaining work. One symbol-year failing doesn't abort the rest -- failures
are collected and reported at the end.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple

from tam.marketdata.completeness import compute_completeness, completeness_sidecar_suffix
from tam.marketdata.store import R2MinuteBarStore

DEFAULT_WORKERS = 8


def _backfill_one(store: R2MinuteBarStore, symbol: str, year: int, force: bool) -> str:
    """One unit of work -- safe to run concurrently across threads against
    the same store/client. Returns "written" or "skipped"; raises on a
    real failure (network error, missing pandas_market_calendars, etc.)
    for the caller to catch and report without aborting the rest."""
    sidecar_key = f"{store._symbol_prefix(symbol)}{year}{completeness_sidecar_suffix()}"
    if not force and store._object_exists(sidecar_key):
        return "skipped"

    df = store._read_object(store._key(symbol, year))
    index = compute_completeness(symbol, year, df)
    if index is None:
        raise RuntimeError("pandas_market_calendars isn't installed -- install the `marketdata` extra and retry.")

    store.write_completeness_bytes(symbol, year, index.to_json().encode("utf-8"))
    return "written"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--symbol", action="append", dest="symbols", help="Backfill only this symbol (repeatable) -- default: every symbol in the bucket"
    )
    parser.add_argument("--force", action="store_true", help="Recompute even if a sidecar already exists")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"Concurrent worker threads (default: {DEFAULT_WORKERS})")
    args = parser.parse_args()

    store = R2MinuteBarStore()
    symbols = args.symbols or store.list_symbols()
    print(f"Found {len(symbols)} symbol(s). Listing years...")

    jobs: List[Tuple[str, int]] = [(symbol, year) for symbol in symbols for year in store._partition_years(symbol)]
    print(f"{len(jobs)} symbol-year(s) to check, using {args.workers} worker thread(s).")

    written = 0
    skipped = 0
    failed: List[Tuple[str, int, Exception]] = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_backfill_one, store, symbol, year, args.force): (symbol, year) for symbol, year in jobs}
        for i, future in enumerate(as_completed(futures), 1):
            symbol, year = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 -- report and keep going; one bad symbol-year shouldn't abort the whole backfill
                failed.append((symbol, year, exc))
                print(f"  [{i}/{len(jobs)}] {symbol} {year}: FAILED -- {exc}")
                continue

            if result == "written":
                written += 1
            else:
                skipped += 1
            if i % 25 == 0 or i == len(jobs):
                print(f"  [{i}/{len(jobs)}] ... ({written} written, {skipped} skipped so far)")

    print(f"Done. Wrote {written} sidecar(s), skipped {skipped} already up to date, {len(failed)} failed.")
    for symbol, year, exc in failed:
        print(f"  FAILED {symbol} {year}: {exc}")


if __name__ == "__main__":
    main()
