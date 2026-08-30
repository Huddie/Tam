"""Ingests the six Massive/Polygon reference datasets (splits, dividends,
IPOs, short volume, short interest, float) into the R2 Parquet lake --
see tam/marketdata/reference_ingest.py for the incremental (append-only,
cursor-based) vs full-refresh logic per dataset.

Usage:
    python -m examples.ingest_reference_data                    # R2, real credentials
    python -m examples.ingest_reference_data --local-root data  # local dev/dry-run

No date range to pass -- each append-only dataset (splits/dividends/
short_volume/short_interest) resumes from its own stored cursor
automatically; re-running this after an already-successful run is a fast,
safe no-op for those and a small, harmless re-fetch for the two
full-refresh ones (ipos/float).

Needs MASSIVE_API_KEY (the REST bearer token -- a different Massive
product surface than the MASSIVE_S3_ACCESS_KEY_ID/MASSIVE_S3_SECRET_ACCESS_KEY
flat-file credentials examples/ingest_minute_bars.py uses) plus the usual
R2 credentials, unless --local-root is given.
"""

from __future__ import annotations

import argparse

from tam.marketdata.reference_ingest import ingest_reference_data
from tam.marketdata.reference_provider import MassiveReferenceProvider
from tam.marketdata.reference_store import LocalReferenceStore, R2ReferenceStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--local-root", default=None, help="Local directory to read/write instead of R2 (dry-run/tests)"
    )
    args = parser.parse_args()

    provider = MassiveReferenceProvider()
    store = LocalReferenceStore(args.local_root) if args.local_root else R2ReferenceStore()

    ingest_reference_data(provider, store, log=print)


if __name__ == "__main__":
    main()
