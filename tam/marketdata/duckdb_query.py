"""DuckDB query access to the Parquet lake -- open_duckdb() is the one call
local dev, Colab, or anywhere else DuckDB runs needs: it wires up `httpfs`
against R2 (tam.marketdata.filesystem.configure_duckdb_r2) or a plain local
directory, and registers a handful of reusable SQL macros for the rollups
tam.marketdata is explicitly built to NOT precompute/store (daily/weekly/
monthly/N-minute bars, returns, rolling volatility) -- every macro derives
its result from the 1-minute Parquet files on demand, per this project's own
"cheap/easy to derive, not precomputed" design goal.

    from tam.marketdata.duckdb_query import open_duckdb

    con = open_duckdb(bucket="tam-market-data")          # reads from R2
    con.sql("SELECT * FROM daily_bars('SPY') ORDER BY day").df()
    con.sql("SELECT * FROM rollup_bars('SPY', 5) ORDER BY bucket").df()
    con.sql("SELECT * FROM rolling_volatility('SPY', 21) ORDER BY day").df()

    con = open_duckdb(local_root="data/minute")          # reads local Parquet instead
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .credentials import R2Credentials, resolve_r2_credentials
from .filesystem import configure_duckdb_r2, r2_uri

if TYPE_CHECKING:
    import duckdb

_MACROS = """
CREATE OR REPLACE MACRO minute_bars(sym) AS TABLE
    SELECT * FROM read_parquet(getvariable('minute_root') || '/' || upper(sym) || '/*.parquet');

CREATE OR REPLACE MACRO daily_bars(sym) AS TABLE
    SELECT symbol, date_trunc('day', ts) AS day,
           first(open ORDER BY ts) AS open, max(high) AS high, min(low) AS low,
           last(close ORDER BY ts) AS close, sum(volume) AS volume
    FROM minute_bars(sym) GROUP BY symbol, day;

CREATE OR REPLACE MACRO weekly_bars(sym) AS TABLE
    SELECT symbol, date_trunc('week', ts) AS week,
           first(open ORDER BY ts) AS open, max(high) AS high, min(low) AS low,
           last(close ORDER BY ts) AS close, sum(volume) AS volume
    FROM minute_bars(sym) GROUP BY symbol, week;

CREATE OR REPLACE MACRO monthly_bars(sym) AS TABLE
    SELECT symbol, date_trunc('month', ts) AS month,
           first(open ORDER BY ts) AS open, max(high) AS high, min(low) AS low,
           last(close ORDER BY ts) AS close, sum(volume) AS volume
    FROM minute_bars(sym) GROUP BY symbol, month;

CREATE OR REPLACE MACRO rollup_bars(sym, interval_minutes) AS TABLE
    SELECT symbol, time_bucket((interval_minutes * INTERVAL 1 MINUTE), ts) AS bucket,
           first(open ORDER BY ts) AS open, max(high) AS high, min(low) AS low,
           last(close ORDER BY ts) AS close, sum(volume) AS volume
    FROM minute_bars(sym) GROUP BY symbol, bucket;

CREATE OR REPLACE MACRO daily_returns(sym) AS TABLE
    SELECT day, close, close / lag(close) OVER (ORDER BY day) - 1 AS return
    FROM daily_bars(sym);

CREATE OR REPLACE MACRO rolling_volatility(sym, window_days) AS TABLE
    SELECT day, return,
           stddev(return) OVER (
               ORDER BY day ROWS BETWEEN (window_days - 1) PRECEDING AND CURRENT ROW
           ) * sqrt(252) AS annualized_vol
    FROM daily_returns(sym);
"""


def _register_macros(con: "duckdb.DuckDBPyConnection") -> None:
    con.sql(_MACROS)


def open_duckdb(
    *,
    bucket: Optional[str] = None,
    credentials: Optional[R2Credentials] = None,
    local_root: Optional[str] = None,
    minute_prefix: str = "minute",
) -> "duckdb.DuckDBPyConnection":
    """A fresh DuckDB connection ready to query the minute-bar lake.

    Reads from R2 by default -- `credentials` resolves the usual way
    (tam.marketdata.credentials.resolve_r2_credentials: kwarg -> env var ->
    Colab secret -> saved file) if not given explicitly; `bucket` overrides
    just the credentials' own bucket (handy for pointing at a `-dev`/test
    bucket without touching the rest of your saved/env credentials).

    Pass `local_root` instead (a plain local directory containing
    `<root>/<minute_prefix>/<SYMBOL>/<year>.parquet`, i.e. whatever
    `LocalMinuteBarStore(root)` wrote) to query local Parquet with no R2/
    network involved at all -- what tests and local dev use.
    """
    import duckdb

    con = duckdb.connect()
    if local_root is not None:
        minute_root = f"{local_root.rstrip('/')}/{minute_prefix}"
    else:
        resolved = credentials or resolve_r2_credentials()
        if bucket is not None:
            resolved = R2Credentials(
                account_id=resolved.account_id,
                access_key_id=resolved.access_key_id,
                secret_access_key=resolved.secret_access_key,
                bucket=bucket,
            )
        configure_duckdb_r2(con, resolved)
        minute_root = r2_uri(resolved, minute_prefix)

    con.sql(f"SET VARIABLE minute_root = '{minute_root}'")
    _register_macros(con)
    return con
