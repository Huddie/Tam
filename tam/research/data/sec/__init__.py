"""tam.research.data.sec -- SEC XBRL/filings data lake: R2 is the durable
cache, SEC's own JSON APIs are the source of truth, EdgarTools' concept-
standardization table does the raw-concept-name-to-line-item mapping (see
normalize.py's own docstring for why, and why NOT its heavier per-filing
XBRL-parsing path for the raw facts themselves).

    from tam.research.data.sec import SEC

    sec = SEC()                                    # self-service TAM_PAT token, no admin R2 credentials needed
    sec.financials(tickers=["AAPL", "MSFT"], start=2015)
    sec.filings(ticker="AAPL", forms=["10-K", "10-Q"], start="2015-01-01")
    sec.query("SELECT cik, fiscal_year, value FROM sec_stmt('income_statement') WHERE line_item = 'revenue'")

    sec = SEC(local_root="data")                   # reads local Parquet instead -- no network
    sec = SEC(bucket="tam-data")                   # raw R2 account credentials instead of a personal token

Thin wrappers over the SQL macros tam.marketdata.duckdb_query.open_duckdb()
registers (sec_facts/sec_financials/sec_stmt/sec_filings/sec_cik) -- see
that module's own docstring for the full macro set and how ticker-or-CIK
resolution works. Not exposed as `tam.SEC` at the top level (unlike
`tam.Secrets`/`tam.Fred`) -- normalize.py imports edgartools' concept-
standardization table eagerly, a heavier dependency than fredapi, so
`import tam` itself should stay cheap; use this explicit submodule import
instead.
"""
from __future__ import annotations

from typing import Any, List, Optional, Sequence, Union

import pandas as pd

from .manifest import Manifest
from .normalize import normalize_facts
from .provider import SecProvider
from .store import SecStore

__all__ = ["SEC", "SecStore", "SecProvider", "Manifest", "normalize_facts"]


class SEC:
    """Holds one lazily-created SQL connection and builds parameterized SQL
    against its sec_* macros -- every `tickers`/`forms`/`start`/`end`
    value is bound as a real DuckDB query parameter (`?`), never string-
    interpolated into the SQL text, so an odd ticker/form string can't
    corrupt or inject into the query.

    Connection resolution is a chain, same shape as tam.marketdata.
    explorer_client.resolve_token()'s own (see that module's own
    docstring): an explicit override (`local_root=` or any
    tam.marketdata.duckdb_query.open_duckdb() kwarg, e.g. `bucket=`) wins
    outright if given -- otherwise a `TAM_PAT` personal token (explicit
    `token=` -> env var/.env -> Colab secret -> saved
    ~/.config/tam-data-explorer/token), the same self-service, READ-ONLY
    path this project recommends for daily_bars/eod_bars in an ordinary
    notebook. `SEC` never writes anything (every method here is a SELECT;
    ingestion is scripts/backfill_sec_facts.py's/SecStore's job, using
    real R2 admin credentials, a completely separate concern) -- so a
    read-only token is exactly the right amount of access, not a
    limitation to work around. Raises a clear, actionable error if
    NEITHER an explicit override nor a token resolves to anything."""

    def __init__(
        self,
        *,
        token: Optional[str] = None,
        api_url: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
        local_root: Optional[str] = None,
        **open_duckdb_kwargs: Any,
    ):
        self._token = token
        self._api_url = api_url
        self._ttl_seconds = ttl_seconds
        self._local_root = local_root
        self._open_duckdb_kwargs = open_duckdb_kwargs
        self._con = None

    def _connect(self):
        if self._con is None:
            if self._local_root is not None or self._open_duckdb_kwargs:
                # Explicit local_root (tests, local dev) or raw R2
                # credentials/bucket override requested -- wins outright,
                # same as tam.marketdata.duckdb_query's own module
                # docstring recommends for ingestion scripts.
                from ....marketdata.duckdb_query import open_duckdb

                self._con = open_duckdb(local_root=self._local_root, **self._open_duckdb_kwargs)
            else:
                # Default: the same self-service TAM_PAT token path
                # NOTEBOOK.md recommends for daily_bars/eod_bars -- mints a
                # short-lived, read-only R2 credential behind the scenes,
                # no raw R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/... needed. Only
                # tam.marketdata.explorer_client's own SqlConnection
                # actually knows how to refresh that credential as it
                # nears expiry, which is why this delegates to it instead
                # of duplicating that logic here. No further fallback --
                # silently reading whatever happens to be in a local
                # `data/` directory if the token isn't configured would be
                # more likely to confuse (stale/unrelated local fixtures)
                # than help; better to fail clearly right here.
                from ....marketdata.explorer_client import connect, resolve_token

                token = resolve_token(self._token, required=False)
                if token is None:
                    raise RuntimeError(
                        "No TAM_PAT personal token found (checked an explicit token=, the TAM_PAT "
                        "environment variable/.env file, a Colab secret, and "
                        "~/.config/tam-data-explorer/token). Pick one:\n"
                        "  1. Pass token=... directly, or set the TAM_PAT environment variable "
                        "(create one at https://data.tamquant.com/settings/tokens).\n"
                        "  2. Pass local_root=... pointing at a local Parquet tree (containing sec/).\n"
                        "  3. Pass bucket=... plus R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY "
                        "env vars for raw R2 admin access."
                    )
                self._con = connect(token=token, api_url=self._api_url, ttl_seconds=self._ttl_seconds)
        return self._con

    def _resolve_ciks(self, tickers: Sequence[Union[str, int]]) -> List[int]:
        """Resolves every one of `tickers` to its real integer CIK via ONE
        small query against the (tiny) reference table, BEFORE building
        the main financials()/filings() query -- so that query can bind
        the real ints directly into its own WHERE clause instead of
        calling sec_cik(...) inline against the big scan. Confirmed live
        via EXPLAIN: a bound int gets pushed all the way into the Parquet
        scan as a real row-group-pruning filter ("Filters: cik=..."),
        while sec_cik(?) called inline forces a runtime join/subquery
        DuckDB can't push into the scan at all -- the difference between
        fetching a few matching row groups over the network and pulling
        every row of every file first, then filtering."""
        placeholders = ", ".join("sec_cik(?)" for _ in tickers)
        row = self._connect().execute(f"SELECT {placeholders}", [str(t) for t in tickers]).fetchone()
        return list(row)

    def query(self, sql: str) -> pd.DataFrame:
        """Raw SQL access to every sec_* macro (and minute_bars/eod_bars,
        since it's the SAME connection) -- `sec.query("SELECT cik,
        fiscal_year, value FROM sec_stmt('income_statement') WHERE
        line_item = 'revenue'")`."""
        return self._connect().sql(sql).df()

    def financials(
        self,
        tickers: Optional[Sequence[Union[str, int]]] = None,
        statement: Optional[str] = None,
        line_items: Optional[Sequence[str]] = None,
        start: Optional[Union[str, int]] = None,
        end: Optional[Union[str, int]] = None,
        dedupe_periods: bool = True,
    ) -> pd.DataFrame:
        """Normalized financials (long format: one row per line item), for
        any combination of `tickers` (tickers or raw CIKs, mixed freely),
        `statement` ("income_statement"/"balance_sheet"/"cash_flow", ...),
        `line_items` ("revenue"/"net_income"/...), and a `fiscal_year`
        range via `start`/`end`. Omitting all of them returns every
        company/period on record.

        `start_date`/`end_date`/`filed_date` come back as real dates --
        cast in the SQL DuckDB runs, not pandas afterward -- and rows are
        pre-sorted by (cik, line_item, end_date); no post-fetch
        pd.to_datetime()/sort_values() needed.

        `dedupe_periods=True` (the default): a single filing often
        reports BOTH a discrete-quarter figure and a year-to-date
        cumulative one under the SAME end_date for the same line item --
        SEC's own fiscal_year/fiscal_period labels don't distinguish them
        (see normalize.py's own docstring). This keeps only the SHORTEST
        reported duration per (cik, line_item, end_date) -- the discrete
        period -- via a window function, pushed into the query itself,
        not a pandas groupby after fetching. Pass False to get every
        period SEC reported, duplicates and all (e.g. if you specifically
        want the YTD figures too)."""
        where: List[str] = []
        params: List[Any] = []

        if tickers:
            ciks = self._resolve_ciks(tickers)
            placeholders = ", ".join("?" for _ in ciks)
            where.append(f"cik IN ({placeholders})")
            params.extend(ciks)
        if statement:
            where.append("statement = ?")
            params.append(statement)
        if line_items:
            placeholders = ", ".join("?" for _ in line_items)
            where.append(f"line_item IN ({placeholders})")
            params.extend(line_items)
        if start is not None:
            where.append("fiscal_year >= ?")
            params.append(int(start))
        if end is not None:
            where.append("fiscal_year <= ?")
            params.append(int(end))

        clause = f"WHERE {' AND '.join(where)}" if where else ""
        base = f"""
            SELECT cik, fiscal_year, fiscal_period,
                   try_cast(start_date AS DATE) AS start_date,
                   try_cast(end_date AS DATE) AS end_date,
                   accession_number,
                   try_cast(filed_date AS DATE) AS filed_date,
                   statement, line_item, concept, value
            FROM sec_financials()
            {clause}
        """
        if dedupe_periods:
            sql = f"""
                SELECT * EXCLUDE (_period_rank) FROM (
                    SELECT *, row_number() OVER (
                        PARTITION BY cik, line_item, end_date
                        ORDER BY end_date - start_date
                    ) AS _period_rank
                    FROM ({base})
                )
                WHERE _period_rank = 1
                ORDER BY cik, line_item, end_date
            """
        else:
            sql = f"{base} ORDER BY cik, line_item, end_date, start_date"

        return self._connect().execute(sql, params).df()

    def filings(
        self,
        ticker: Optional[Union[str, int]] = None,
        forms: Optional[Sequence[str]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """Filing metadata (accession number, form, filed date, period of
        report, ...) for one company, optionally scoped to specific
        `forms` and a filed-date range. `filed_date`/`period_of_report`
        come back as real dates (cast in SQL), rows pre-sorted
        chronologically."""
        where: List[str] = []
        params: List[Any] = []

        if ticker is not None:
            where.append("cik = ?")
            params.append(self._resolve_ciks([ticker])[0])
        if forms:
            placeholders = ", ".join("?" for _ in forms)
            where.append(f"form IN ({placeholders})")
            params.extend(forms)
        if start is not None:
            where.append("filed_date >= ?")
            params.append(str(start))
        if end is not None:
            where.append("filed_date <= ?")
            params.append(str(end))

        clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"""
            SELECT cik, accession_number, form,
                   try_cast(filed_date AS DATE) AS filed_date,
                   try_cast(period_of_report AS DATE) AS period_of_report,
                   primary_document, is_xbrl
            FROM sec_filings()
            {clause}
            ORDER BY filed_date
        """
        return self._connect().execute(sql, params).df()
