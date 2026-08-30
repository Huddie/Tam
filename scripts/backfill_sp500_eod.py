"""Backfills ~20 years of daily EOD bars (via tam.data's yfinance-backed
DataRepository) for every ticker that has EVER been an S&P 500 constituent
in that window -- not just today's ~503, since membership churns over 20
years and using only today's list would introduce survivorship bias (same
reasoning as tam.basket.universe's PitIndexUniverse docstring).

Usage:
    uv run python scripts/backfill_sp500_eod.py
    uv run python scripts/backfill_sp500_eod.py --years 15
    uv run python scripts/backfill_sp500_eod.py --root data/eod --workers 4
    uv run python scripts/backfill_sp500_eod.py --no-r2   # local disk only

Gets the ticker list from pitindex's bundled offline dataset (no network for
the list itself -- see tam/basket/universe.py's PitIndexUniverse; needs the
`pitindex` extra: `uv sync --extra pitindex`). Fetching from yfinance is
effectively SEQUENTIAL regardless of --workers (see tam/data/providers.py's
_YFINANCE_LOCK docstring -- concurrent yf.download() calls for different
tickers have been observed to silently race and return the WRONG ticker's
data) -- one call per ticker, ~1000 tickers, is a few-minutes job, not an
overnight one.

Writes to BOTH the local --root AND R2 (eod/<SYMBOL>/<year>.parquet in the
same tam-data bucket tam.marketdata already uses, under its own "eod/"
prefix -- see tam.data.storage.R2DataStore) by default, fanned out via
MultiDataStore so each ticker is only fetched from yfinance ONCE, not once
per destination. R2 credentials resolve the usual way (tam.marketdata.
credentials.resolve_r2_credentials) -- pass --no-r2 to skip R2 entirely if
you don't have them configured.

Safe to re-run or interrupt: DataRepository.ingest() only fetches date
sub-ranges not already on disk (tam/data/repository.py's _missing_ranges),
so restarting just resumes instead of re-fetching everything -- checked
against the PRIMARY store only (--root's local disk), same as
MultiDataStore's own read()/exists() contract.
"""

from __future__ import annotations

import argparse
import time
from datetime import date

from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.storage import DataStore, MultiDataStore
from tam.registry import Registry

BATCH_SIZE = 20  # tickers per ingest() call -- just for periodic progress output, not a correctness boundary


def _historical_sp500_tickers(years: int) -> tuple[list[str], date, date]:
    """Every ticker that was ever an S&P 500 constituent in the last `years`
    years -- the union across pitindex's own change-date snapshots
    (get_constituents_history), not just today's list."""
    import pitindex

    end = date.today()
    start = end.replace(year=end.year - years)
    history = pitindex.get_constituents_history(start, end, index="sp500")
    return sorted(history["ticker"].unique().tolist()), start, end


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--years", type=int, default=20, help="How many years of history to backfill (default: 20)")
    parser.add_argument(
        "--root",
        default="data/eod",
        help="Local DataStore root (default: data/eod, matching the existing on-disk layout)",
    )
    parser.add_argument("--no-r2", action="store_true", help="Skip R2 entirely -- write to --root only")
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Thread pool size handed to DataRepository.ingest() (default: 8 -- see module docstring for why "
        "yfinance fetches themselves stay effectively sequential regardless of this)",
    )
    args = parser.parse_args()

    tickers, start, end = _historical_sp500_tickers(args.years)
    print(f"{len(tickers)} S&P 500 ticker(s) ever a constituent in [{start}, {end}].")

    provider: DataProvider = Registry.get(DataProvider, "yfinance")
    local_store: DataStore = Registry.create(DataStore, "parquet", args.root)
    stores = [local_store]
    if not args.no_r2:
        stores.append(Registry.create(DataStore, "r2_parquet"))
        print("Writing to local disk AND R2 (eod/<SYMBOL>/<year>.parquet).")
    else:
        print(f"Writing to local disk only ({args.root}).")
    store: DataStore = MultiDataStore(stores) if len(stores) > 1 else local_store
    repo = DataRepository(provider, store)

    started = time.monotonic()
    for i in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[i : i + BATCH_SIZE]
        repo.ingest(batch, start, end, max_workers=args.workers)
        done = min(i + BATCH_SIZE, len(tickers))
        elapsed = time.monotonic() - started
        print(f"  [{done}/{len(tickers)}] ... ({elapsed:.0f}s elapsed)")

    print(f"Done in {time.monotonic() - started:.0f}s.")


if __name__ == "__main__":
    main()
