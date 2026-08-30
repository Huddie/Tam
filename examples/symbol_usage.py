"""Demonstrates tam.Symbol/tam.query()/tam.Cache end to end -- run against a
local Parquet tree (no R2/network needed) by default; point --bucket at a
real R2 bucket for the same calls against live data.

    python -m examples.symbol_usage --local-root data
    python -m examples.symbol_usage --bucket tam-data
"""

from __future__ import annotations

import argparse

from tam import CIK, Engine, ManualCache, Symbol, query


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-root", default=None, help="Local Parquet tree instead of R2")
    parser.add_argument("--bucket", default=None, help="R2 bucket (raw account credentials, not a personal token)")
    args = parser.parse_args()
    connection_kwargs = {}
    if args.local_root:
        connection_kwargs["local_root"] = args.local_root
    elif args.bucket:
        connection_kwargs["bucket"] = args.bucket

    # A cache constructed once and reused across every call below -- the
    # second identical call (there isn't one in this script, but see the
    # comment at the bottom) would skip the connection entirely.
    cache = ManualCache()

    aapl = Symbol("AAPL", cache=cache, **connection_kwargs)
    print("minute bars:", len(aapl.minute_bars()), "rows")
    print("splits:", len(aapl.splits()), "rows")
    print("short volume:", len(aapl.short_volume()), "rows")
    print("financials (income statement):", len(aapl.financials(statement="income_statement")), "rows")

    # Same AAPL, identified by its SEC CIK instead of the ticker -- resolved
    # automatically, mixes freely with plain ticker strings.
    print("splits via CIK:", len(Symbol(CIK(320193), **connection_kwargs).splits()), "rows")

    # columns= selects a subset instead of every column.
    print("splits (columns=):", aapl.splits(columns=["ticker", "execution_date"]).columns.tolist())

    # engine=Engine.POLARS (or the plain string "polars") returns a polars
    # DataFrame instead of pandas -- requires polars to be installed.
    try:
        import polars  # noqa: F401

        print("splits (polars):", type(aapl.splits(engine=Engine.POLARS)).__name__)
    except ImportError:
        print("polars not installed -- skipping the engine=Engine.POLARS demo")

    basket = Symbol("AAPL", "MSFT", "NVDA", **connection_kwargs)
    print("basket short volume (one query, not three):", len(basket.short_volume()), "rows")

    print("raw SQL via tam.query():", query("SELECT count(*) AS n FROM splits()", **connection_kwargs))

    # Re-running any of the aapl.* calls above with the SAME arguments
    # would now be served from `cache` instead of hitting the connection
    # again -- exactly the behavior you want for re-running a notebook
    # cell without re-fetching.


if __name__ == "__main__":
    main()
