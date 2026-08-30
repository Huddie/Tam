"""Reports which expected NYSE trading days in a marketdata: config's
start/end range are actually recorded as ingested (per the resumability
manifest tam.marketdata.ingest already writes) -- answers "did every day
actually make it in" without needing day-partitioned storage or browsing R2
by hand. Missing days are exactly what re-running the same ingest config
will pick up (resumable -- only refetches what's missing, nothing else).

Usage:
    python -m examples.check_ingest_coverage examples/ingest_minute_bars_5yr_config.yaml
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tam.marketdata.ingest import run_coverage_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Same marketdata: YAML config used for ingestion")
    args = parser.parse_args()

    report = run_coverage_report(args.config)
    print(f"{len(report.ingested)}/{len(report.expected)} expected trading days ingested.")
    if report.missing:
        print(f"{len(report.missing)} missing:")
        for day in report.missing:
            print(f"  {day}")
    else:
        print("No gaps -- fully ingested for this range.")


if __name__ == "__main__":
    main()
