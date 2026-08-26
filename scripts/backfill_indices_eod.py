"""Backfills ~20 years of daily EOD bars (via tam.data's yfinance-backed
DataRepository) for a small, curated list of common market indices and the
ETFs that track them -- unlike scripts/backfill_sp500_eod.py's point-in-time
constituent universe, this list is FIXED (see COMMON_INDEX_TICKERS below;
edit it directly to add/remove tickers -- there's no single canonical feed
of "every common index" the way pitindex covers S&P 500 membership).

Includes both the raw index itself (Yahoo's "^" prefix -- quote-only, not
tradeable: ^GSPC/^DJI/^IXIC/^NDX/^RUT/^VIX) and its most common tracking
ETF(s), plus the leveraged/inverse ETFs explicitly asked for (TQQQ/SQQQ and
their S&P 500 equivalents). Some of these (e.g. TQQQ, launched 2010) don't
actually have 20 years of history -- yfinance/DataRepository just returns
whatever's actually available, no error for a shorter real history.

Usage:
    uv run python scripts/backfill_indices_eod.py
    uv run python scripts/backfill_indices_eod.py --years 15
    uv run python scripts/backfill_indices_eod.py --no-r2

Writes to BOTH local disk (--root, default data/eod) AND R2 by default --
same MultiDataStore fan-out as scripts/backfill_sp500_eod.py, so see that
script's own docstring for why (one fetch per ticker, not one per
destination).
"""
from __future__ import annotations

import argparse
import time
from datetime import date

from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.storage import DataStore, MultiDataStore
from tam.registry import Registry

# Edit this list directly to add/remove tickers -- a fixed set, not derived
# from any membership feed (unlike backfill_sp500_eod.py's pitindex-based
# universe), since "the major indices and their ETFs" doesn't churn the way
# S&P 500 constituents do.
COMMON_INDEX_TICKERS = [
    # Raw indices (Yahoo's "^" prefix) -- quote-only, not tradeable.
    "^GSPC",  # S&P 500
    "^DJI",  # Dow Jones Industrial Average
    "^IXIC",  # Nasdaq Composite
    "^NDX",  # Nasdaq-100
    "^RUT",  # Russell 2000
    "^VIX",  # CBOE Volatility Index
    # Broad-market tracking ETFs
    "SPY",
    "VOO",
    "IVV",  # S&P 500
    "QQQ",  # Nasdaq-100
    "DIA",  # Dow Jones Industrial Average
    "IWM",  # Russell 2000
    "VTI",  # Total US market
    # Leveraged/inverse
    "TQQQ",
    "SQQQ",  # 3x Nasdaq-100 bull/bear
    "UPRO",
    "SPXU",  # 3x S&P 500 bull/bear
    "SSO",
    "SDS",  # 2x S&P 500 bull/bear
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--years", type=int, default=20, help="How many years of history to backfill (default: 20)")
    parser.add_argument("--root", default="data/eod", help="Local DataStore root (default: data/eod)")
    parser.add_argument("--no-r2", action="store_true", help="Skip R2 entirely -- write to --root only")
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Thread pool size (default: 8 -- see backfill_sp500_eod.py's docstring on why yfinance fetches "
        "themselves stay effectively sequential regardless of this)",
    )
    args = parser.parse_args()

    end = date.today()
    start = end.replace(year=end.year - args.years)
    print(f"{len(COMMON_INDEX_TICKERS)} ticker(s) in [{start}, {end}].")

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
    for i, ticker in enumerate(COMMON_INDEX_TICKERS, 1):
        repo.ingest([ticker], start, end, max_workers=args.workers)
        print(f"  [{i}/{len(COMMON_INDEX_TICKERS)}] {ticker} ... ({time.monotonic() - started:.0f}s elapsed)")
    print(f"Done in {time.monotonic() - started:.0f}s.")


if __name__ == "__main__":
    main()
