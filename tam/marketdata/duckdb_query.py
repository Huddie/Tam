"""DuckDB query access to the Parquet lake -- open_duckdb() is the one call
local dev, Colab, or anywhere else DuckDB runs needs: it wires up `httpfs`
against R2 (tam.marketdata.filesystem.configure_duckdb_r2) or a plain local
directory, and registers a handful of reusable SQL macros for the rollups
tam.marketdata is explicitly built to NOT precompute/store (daily/weekly/
monthly/N-minute bars, returns, rolling volatility) -- every rollup macro
derives its result from the 1-minute Parquet files on demand, per this
project's own "cheap/easy to derive, not precomputed" design goal.

Also registers `eod_bars(sym)`, reading tam.data's OWN end-of-day Parquet
lake (eod/<SYMBOL>/<year>.parquet -- see tam.data.storage.R2DataStore) --
NOT derived from the minute bars, so unlike daily_bars(sym) it carries a
real yfinance adj_close (dividend/split-adjusted) and generally covers a
much longer/different symbol history (minute bars only go back as far as
the flat-file vendor's own retention; EOD via yfinance can go back decades).

Also registers the SEC macros (`sec_facts`, `sec_financials`, `sec_stmt`,
`sec_filings`, `sec_companies`) over tam.research.data.sec's OWN lake
(sec/... -- raw XBRL facts, normalized financials, filing metadata; see
that subpackage for the full schema/partitioning rationale). Every one
except `sec_companies` (the ticker/CIK/name reference table itself, with
nothing to scope it by) accepts an OPTIONAL `ticker_or_cik` (a ticker
string, a raw CIK int, or a CIK-as-string -- resolved via `sec_cik()`,
verified against real data before writing this): omit it for every
company at once (narrow yourself with `WHERE cik = ...`), or pass it to
scope to one company. NOT overloaded by argument count --
DuckDB's CREATE MACRO doesn't support that (confirmed live:
`CatalogException: already exists` on a second same-name macro even with
a different arity) -- one signature with a default covers both shapes.

Also registers six Massive/Polygon reference-data macros -- `splits`,
`dividends`, `ipos` over `tam.marketdata.reference_store`'s
`corporate_actions/` prefix, and `short_volume`, `short_interest`,
`float_data` over its `positioning/` prefix (see that module's own
docstring for why grouped this way, and reference_ingest.py for how they
get there). Every one takes an OPTIONAL `sym` (a plain ticker string, no
CIK resolution needed -- these datasets store ticker as a plain column,
not by CIK): omit it for every ticker at once, or pass it to scope to one.

`short_volume`/`short_interest` are partitioned per-ticker on disk
(`positioning/short_volume/<TICKER>/<year>.parquet`, same layout as
`minute/<SYMBOL>/<year>.parquet` -- see reference_store.py's own
docstring for why only these two, not all six) -- their macros build
`sym` (or `*` when omitted) directly into the glob's PATH rather than
filtering with a `WHERE ticker = ...` clause afterward, so passing a
`sym` narrows the actual files scanned instead of reading every ticker's
data and discarding most of it. `splits`/`dividends`/`ipos`/`float_data`
stay single global files, so those four still filter with `WHERE` after
reading everything -- there's nothing to narrow the scan to.

Every one of those macros' read_parquet() calls references its own `sym`/
`ticker_or_cik` parameter directly inside the glob path expression --
NOT dead code. Confirmed live: DuckDB eagerly resolves (and errors on
zero matches for) a table macro's read_parquet() glob AT `CREATE MACRO`
TIME, UNLESS the path expression textually references the macro's own
parameter somewhere -- purely syntactic, not about the actual resolved
value (a defaulted `sym := 'X'` used directly in a path still defers; an
unrelated parameter used only in a later `WHERE` clause does not). The
four single-global-file macros (`sec_facts`/`sec_financials`/`splits`/
etc.) use a no-op `substr(coalesce(try_cast(sym AS VARCHAR), ''), 1, 0)`
suffix (always an empty string) purely to satisfy this syntactic
requirement, since their path doesn't otherwise need the parameter;
`short_volume`/`short_interest` don't need that trick since their
`CASE WHEN sym IS NULL THEN '*' ELSE upper(sym) END` is ALREADY a real,
meaningful part of the path. Without either form, `open_duckdb()` against
a bucket/local_root that doesn't have the relevant prefix YET (e.g.
before the first backfill has ever run) would fail to even construct a
connection at all -- caught live by this project's own pre-existing
marketdata test suite the first time these macros shipped.

All three lakes live in the same bucket under different prefixes
("minute/"/"eod/"/"sec/"), so one open_duckdb() call queries all of them::

    from tam.marketdata.duckdb_query import open_duckdb

    con = open_duckdb(bucket="tam-data")          # reads from R2
    con.sql("SELECT * FROM daily_bars('SPY') ORDER BY day").df()          # from minute bars
    con.sql("SELECT * FROM eod_bars('SPY') ORDER BY date").df()           # true EOD, adj_close included
    con.sql("SELECT * FROM rollup_bars('SPY', 5) ORDER BY bucket").df()
    con.sql("SELECT * FROM rolling_volatility('SPY', 21) ORDER BY day").df()
    con.sql("SELECT * FROM sec_stmt('income_statement', 'AAPL') ORDER BY fiscal_year").df()
    con.sql("SELECT * FROM sec_stmt('income_statement') WHERE line_item = 'revenue'").df()  # every company

    con = open_duckdb(local_root="data")          # reads local Parquet instead (data/minute, data/eod, data/sec)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .credentials import R2Credentials, resolve_r2_credentials
from .filesystem import configure_duckdb_r2, r2_uri

if TYPE_CHECKING:
    import duckdb

_MACROS = """
CREATE OR REPLACE MACRO minute_bars(sym) AS TABLE
    SELECT * FROM read_parquet(getvariable('minute_root') || '/' || upper(sym) || '/*.parquet');

CREATE OR REPLACE MACRO eod_bars(sym) AS TABLE
    SELECT * FROM read_parquet(getvariable('eod_root') || '/' || upper(sym) || '/*.parquet');

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

CREATE OR REPLACE MACRO sec_cik(ticker_or_cik) AS (
    CASE
        WHEN try_cast(ticker_or_cik AS BIGINT) IS NOT NULL THEN try_cast(ticker_or_cik AS BIGINT)
        ELSE (
            SELECT cik FROM read_parquet(
                getvariable('sec_root') || '/reference/company_tickers.parquet'
                || substr(coalesce(try_cast(ticker_or_cik AS VARCHAR), ''), 1, 0)
            )
            WHERE upper(ticker) = upper(try_cast(ticker_or_cik AS VARCHAR)) LIMIT 1
        )
    END
);

CREATE OR REPLACE MACRO sec_facts(ticker_or_cik := NULL) AS TABLE
    SELECT * FROM read_parquet(
        getvariable('sec_root') || '/facts/*/*.parquet'
        || substr(coalesce(try_cast(ticker_or_cik AS VARCHAR), ''), 1, 0)
    )
    WHERE ticker_or_cik IS NULL OR cik = sec_cik(ticker_or_cik);

CREATE OR REPLACE MACRO sec_financials(ticker_or_cik := NULL) AS TABLE
    SELECT * FROM read_parquet(
        getvariable('sec_root') || '/financials/*.parquet'
        || substr(coalesce(try_cast(ticker_or_cik AS VARCHAR), ''), 1, 0)
    )
    WHERE ticker_or_cik IS NULL OR cik = sec_cik(ticker_or_cik);

CREATE OR REPLACE MACRO sec_stmt(sheet_name, ticker_or_cik := NULL) AS TABLE
    SELECT * FROM sec_financials(ticker_or_cik) WHERE statement = sheet_name;

CREATE OR REPLACE MACRO sec_filings(ticker_or_cik := NULL) AS TABLE
    SELECT * FROM read_parquet(
        getvariable('sec_root') || '/submissions/*.parquet'
        || substr(coalesce(try_cast(ticker_or_cik AS VARCHAR), ''), 1, 0)
    )
    WHERE ticker_or_cik IS NULL OR cik = sec_cik(ticker_or_cik);

CREATE OR REPLACE MACRO sec_companies(noop := NULL) AS TABLE
    SELECT * FROM read_parquet(
        getvariable('sec_root') || '/reference/company_tickers.parquet'
        || substr(coalesce(try_cast(noop AS VARCHAR), ''), 1, 0)
    );

CREATE OR REPLACE MACRO splits(sym := NULL) AS TABLE
    SELECT * FROM read_parquet(
        getvariable('corporate_actions_root') || '/splits/*.parquet'
        || substr(coalesce(try_cast(sym AS VARCHAR), ''), 1, 0)
    )
    WHERE sym IS NULL OR ticker = upper(sym);

CREATE OR REPLACE MACRO dividends(sym := NULL) AS TABLE
    SELECT * FROM read_parquet(
        getvariable('corporate_actions_root') || '/dividends/*.parquet'
        || substr(coalesce(try_cast(sym AS VARCHAR), ''), 1, 0)
    )
    WHERE sym IS NULL OR ticker = upper(sym);

CREATE OR REPLACE MACRO ipos(sym := NULL) AS TABLE
    SELECT * FROM read_parquet(
        getvariable('corporate_actions_root') || '/ipos/all.parquet'
        || substr(coalesce(try_cast(sym AS VARCHAR), ''), 1, 0)
    )
    WHERE sym IS NULL OR ticker = upper(sym);

CREATE OR REPLACE MACRO short_volume(sym := NULL) AS TABLE
    SELECT * FROM read_parquet(
        getvariable('positioning_root') || '/short_volume/'
        || CASE WHEN sym IS NULL THEN '*' ELSE upper(sym) END
        || '/*.parquet'
    );

CREATE OR REPLACE MACRO short_interest(sym := NULL) AS TABLE
    SELECT * FROM read_parquet(
        getvariable('positioning_root') || '/short_interest/'
        || CASE WHEN sym IS NULL THEN '*' ELSE upper(sym) END
        || '/*.parquet'
    );

CREATE OR REPLACE MACRO float_data(sym := NULL) AS TABLE
    SELECT * FROM read_parquet(
        getvariable('positioning_root') || '/float/all.parquet'
        || substr(coalesce(try_cast(sym AS VARCHAR), ''), 1, 0)
    )
    WHERE sym IS NULL OR ticker = upper(sym);
"""


def _register_macros(con: duckdb.DuckDBPyConnection) -> None:
    con.sql(_MACROS)


def _configure_connection(con: duckdb.DuckDBPyConnection) -> None:
    """Disables DuckDB's own progress bar, then registers every SQL macro
    via _register_macros(). The progress bar isn't just cosmetic noise --
    confirmed live (a real Colab notebook session) that leaving it
    enabled corrupts a SUBSEQUENT Plotly chart's rendering in the SAME
    cell (an empty chart frame, no traces drawn, no Python-visible error
    anywhere): DuckDB's progress bar writes directly to the underlying
    stdout FILE DESCRIPTOR in native code, bypassing Python's own
    sys.stdout entirely (redirecting sys.stdout never reveals it --
    confirmed live, that capture came back completely empty even while
    the bug was reproducing). Jupyter/Colab's own output capture still
    picks it up at the fd level, and that raw, dynamically-redrawn
    (carriage-return-based) content getting interleaved into the same
    cell's output stream as a LATER rich-display call is what breaks the
    chart. Disabling it here means every open_duckdb()/explorer_client
    connection is safe by default -- nobody querying
    tam.research.data.sec or tam.marketdata from a notebook should have
    to discover this themselves.

    DuckDB's Jupyter-kernel detection requires `ipywidgets` to be
    installed to change this setting AT ALL (even to turn it off) --
    confirmed live: `SET enable_progress_bar = false` itself raises
    `InvalidInputException` inside a real Jupyter kernel without it. Caught
    here rather than propagated so a missing `ipywidgets` degrades to "the
    progress bar might render" instead of "no connection at all."""
    import duckdb

    try:
        con.sql("SET enable_progress_bar = false; SET enable_progress_bar_print = false;")
    except duckdb.InvalidInputException:
        pass
    _register_macros(con)


def open_duckdb(
    *,
    bucket: str | None = None,
    credentials: R2Credentials | None = None,
    local_root: str | None = None,
    minute_prefix: str = "minute",
    eod_prefix: str = "eod",
    sec_prefix: str = "sec",
    corporate_actions_prefix: str = "corporate_actions",
    positioning_prefix: str = "positioning",
) -> duckdb.DuckDBPyConnection:
    """A fresh DuckDB connection ready to query all five Parquet lakes --
    the minute-bar lake (minute_bars(sym) and its rollup macros), tam.data's
    end-of-day lake (eod_bars(sym)), tam.research.data.sec's XBRL/filings
    lake (sec_facts/sec_financials/sec_stmt/sec_filings/sec_companies), and
    the Massive/Polygon reference-data lakes: corporate actions
    (splits/dividends/ipos) and positioning (short_volume/short_interest/
    float_data).

    Reads from R2 by default -- `credentials` resolves the usual way
    (tam.marketdata.credentials.resolve_r2_credentials: kwarg -> env var ->
    Colab secret -> saved file) if not given explicitly; `bucket` overrides
    just the credentials' own bucket (handy for pointing at a `-dev`/test
    bucket without touching the rest of your saved/env credentials).

    Pass `local_root` instead (a plain local directory containing
    `<root>/<minute_prefix>/...`, `<root>/<eod_prefix>/...`,
    `<root>/<sec_prefix>/...`, `<root>/<corporate_actions_prefix>/...`,
    and/or `<root>/<positioning_prefix>/...`) to query local Parquet with
    no R2/network involved at all -- what tests and local dev use.
    Querying a lake that doesn't actually exist under `local_root` is fine
    as long as you don't SELECT from its macro -- read_parquet() only
    globs the path when the macro is actually invoked.
    """
    import duckdb

    con = duckdb.connect()
    if local_root is not None:
        root = local_root.rstrip("/")
        minute_root = f"{root}/{minute_prefix}"
        eod_root = f"{root}/{eod_prefix}"
        sec_root = f"{root}/{sec_prefix}"
        corporate_actions_root = f"{root}/{corporate_actions_prefix}"
        positioning_root = f"{root}/{positioning_prefix}"
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
        eod_root = r2_uri(resolved, eod_prefix)
        sec_root = r2_uri(resolved, sec_prefix)
        corporate_actions_root = r2_uri(resolved, corporate_actions_prefix)
        positioning_root = r2_uri(resolved, positioning_prefix)

    con.sql(f"SET VARIABLE minute_root = '{minute_root}'")
    con.sql(f"SET VARIABLE eod_root = '{eod_root}'")
    con.sql(f"SET VARIABLE sec_root = '{sec_root}'")
    con.sql(f"SET VARIABLE corporate_actions_root = '{corporate_actions_root}'")
    con.sql(f"SET VARIABLE positioning_root = '{positioning_root}'")
    _configure_connection(con)
    return con
