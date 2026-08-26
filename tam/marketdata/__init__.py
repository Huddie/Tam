"""tam.marketdata -- a historical 1-minute OHLCV data lake (Cloudflare R2 +
Parquet + DuckDB), separate from tam.data's end-of-day cache but built with
the same interfaces/registry conventions (see tam.registry): a
MinuteBarProvider fetches raw bars, a MinuteBarStore persists/retrieves them
partitioned by symbol/year (exactly tam.data.storage's own layout, just
generalized from a local pathlib.Path to any pyarrow.fs.FileSystem so the
IDENTICAL store class runs against local disk in tests/dev and against R2 in
production), and ingest() wires a provider + tam.basket.universe.UniverseProvider
(point-in-time S&P 500 constituents -- no new universe abstraction here) +
validation together into a resumable backfill.

Query access does not go through this package at all once data is in R2 --
DuckDB's own httpfs extension reads the Parquet lake directly (see
tam.marketdata.duckdb_query.open_duckdb()), from a local machine, Colab, or
anywhere else, with no API service in between.

    from tam.marketdata.credentials import resolve_r2_credentials
    from tam.marketdata.duckdb_query import open_duckdb

    con = open_duckdb(bucket="tam-data")
    con.sql("SELECT * FROM daily_bars('SPY')").df()
"""
from .schema import MINUTE_BAR_COLUMNS, SYMBOL, TS, empty_minute_bar_frame

__all__ = ["MINUTE_BAR_COLUMNS", "SYMBOL", "TS", "empty_minute_bar_frame"]
