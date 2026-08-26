"""Config-driven historical 1-minute OHLCV backfill: fetch a vendor's bulk
flat files -> filter to SPY + that day's point-in-time S&P 500 constituents
-> validate -> upload into a MinuteBarStore (local disk for testing, R2 in
production). See MARKETDATA.md for full setup (R2/Massive credentials, what
each config field means) and examples/ingest_minute_bars_config.yaml for a
config example.

Usage:
    python -m examples.ingest_minute_bars examples/ingest_minute_bars_config.yaml
"""
from __future__ import annotations

import argparse
from pathlib import Path

from tam.marketdata.ingest import run_ingest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Path to a marketdata: YAML config")
    args = parser.parse_args()

    result = run_ingest(args.config)
    print(
        f"Processed {result.days_processed} day(s); "
        f"skipped {result.days_skipped_already_done} already-done, "
        f"{result.days_skipped_no_data} with no data."
    )


if __name__ == "__main__":
    main()
