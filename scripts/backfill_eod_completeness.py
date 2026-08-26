"""One-time (or re-run-safe) backfill: computes and writes a completeness
sidecar (see tam.data.completeness) for every symbol-year ALREADY in a
tam.data EOD store -- new writes get this automatically now (see
tam.data.storage's _write_completeness hooks on ParquetStore/CsvStore/
R2DataStore), so this script only exists to catch up data written before
that existed (e.g. an already-backfilled local data/eod/, or any symbol-year
a re-run's gap detection won't touch again because it's already fully there).

Usage:
    uv run python scripts/backfill_eod_completeness.py
    uv run python scripts/backfill_eod_completeness.py --no-r2
    uv run python scripts/backfill_eod_completeness.py --force
    uv run python scripts/backfill_eod_completeness.py --symbol AAPL --symbol MSFT

Reads the symbol list the same way scripts/backfill_sp500_eod.py does (every
ticker that was ever an S&P 500 constituent in --years years, via pitindex)
unless --symbol is given explicitly. Runs concurrently via a thread pool --
each unit of work is one read (local disk or R2) plus at most one write,
the same I/O-bound reasoning as scripts/backfill_completeness.py (the
minute-bar analog of this script).
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import List, Tuple

from tam.data.completeness import SCHEMA_VERSION, compute_completeness, sidecar_schema_version
from tam.data.storage import DataStore
from tam.registry import Registry

DEFAULT_WORKERS = 8


def _historical_sp500_tickers(years: int) -> List[str]:
    import pitindex

    end = date.today()
    start = end.replace(year=end.year - years)
    history = pitindex.get_constituents_history(start, end, index="sp500")
    return sorted(history["ticker"].unique().tolist())


def _backfill_symbol(store: DataStore, symbol: str, force: bool) -> Tuple[int, int]:
    """Recomputes/writes a completeness sidecar for every year of `symbol`'s
    history already in `store`. Reads the WHOLE symbol (not per-year) for
    simplicity -- a symbol's full EOD history is tiny (~17KB/year) even
    across 20 years, so this isn't worth a separate per-year read primitive.
    Skips a year whose sidecar is already current unless `force`. Returns
    (written, skipped)."""
    df = store.read(symbol)
    if df.empty:
        return 0, 0

    written = 0
    skipped = 0
    for year, group in df.groupby(df.index.year):
        year = int(year)
        if not force:
            existing = store.read_completeness_bytes(symbol, year)
            if existing is not None and sidecar_schema_version(existing) == SCHEMA_VERSION:
                skipped += 1
                continue
        index = compute_completeness(symbol, year, group)
        if index is not None:
            store.write_completeness_bytes(symbol, year, index.to_json().encode("utf-8"))
            written += 1
    return written, skipped


def _run(store: DataStore, label: str, symbols: List[str], force: bool, workers: int) -> None:
    print(f"Backfilling completeness for {label} ({len(symbols)} symbol(s))...")
    written = 0
    skipped = 0
    failed: List[Tuple[str, Exception]] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_backfill_symbol, store, symbol, force): symbol for symbol in symbols}
        for i, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]
            try:
                w, s = future.result()
            except Exception as exc:  # noqa: BLE001 -- report and keep going; one bad symbol shouldn't abort the rest
                failed.append((symbol, exc))
                print(f"  [{i}/{len(symbols)}] {symbol}: FAILED -- {exc}")
                continue
            written += w
            skipped += s
            if i % 50 == 0 or i == len(symbols):
                print(f"  [{i}/{len(symbols)}] ... ({written} written, {skipped} skipped so far)")

    print(f"{label}: wrote {written} sidecar(s), skipped {skipped} already up to date, {len(failed)} failed.")
    for symbol, exc in failed:
        print(f"  FAILED {symbol}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--symbol", action="append", dest="symbols", help="Backfill only this symbol (repeatable) -- default: every "
        "ticker ever in the S&P 500 in --years years"
    )
    parser.add_argument(
        "--years", type=int, default=20, help="How many years back to resolve the default symbol list over "
        "(default: 20, matching backfill_sp500_eod.py)"
    )
    parser.add_argument("--root", default="data/eod", help="Local DataStore root (default: data/eod)")
    parser.add_argument("--no-r2", action="store_true", help="Skip R2 -- local only")
    parser.add_argument("--no-local", action="store_true", help="Skip local disk -- R2 only")
    parser.add_argument("--force", action="store_true", help="Recompute even if a sidecar is already current")
    parser.add_argument(
        "--workers", type=int, default=DEFAULT_WORKERS, help=f"Concurrent worker threads (default: {DEFAULT_WORKERS})"
    )
    args = parser.parse_args()

    symbols = args.symbols or _historical_sp500_tickers(args.years)
    print(f"{len(symbols)} symbol(s) to check.")

    if not args.no_local:
        local_store: DataStore = Registry.create(DataStore, "parquet", args.root)
        _run(local_store, f"local ({args.root})", symbols, args.force, args.workers)

    if not args.no_r2:
        r2_store: DataStore = Registry.create(DataStore, "r2_parquet")
        _run(r2_store, "R2 (eod/)", symbols, args.force, args.workers)


if __name__ == "__main__":
    main()
