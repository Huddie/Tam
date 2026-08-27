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

from functools import lru_cache
from typing import Any, List, Optional, Sequence, Union

import pandas as pd

from . import schema
from .manifest import Manifest
from .normalize import normalize_facts
from .provider import SecProvider
from .store import SecStore

__all__ = ["SEC", "SecStore", "SecProvider", "Manifest", "normalize_facts"]


def _is_missing_glob_error(exc: Exception, *path_hints: str) -> bool:
    """True if `exc` is DuckDB's own "IO Error: No files found that match
    the pattern ..." -- confirmed live (this session) as the exact,
    consistent wording for a Parquet glob matching zero files, e.g.
    before a given layer's first backfill. If `path_hints` are given,
    they must ALL also appear in the message -- e.g. "reference", to
    distinguish "the reference table itself is missing" (a real
    configuration problem) from "this OTHER layer just has no rows yet"
    (a legitimate, expected state that should return empty, not raise)."""
    message = str(exc)
    if "No files found that match the pattern" not in message:
        return False
    return all(hint in message for hint in path_hints)


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
        # A PER-INSTANCE cache (built here, not a @lru_cache on the method
        # itself) -- decorating the method directly would share ONE cache
        # across every SEC instance ever created, keyed on (self, ticker),
        # which keeps every one of those instances (and its DuckDB
        # connection) alive for the rest of the process. Binding a fresh
        # lru_cache to this instance's own bound method instead means the
        # cache -- and the `self` reference inside it -- is freed the
        # moment this SEC instance is.
        self._resolve_cik = lru_cache(maxsize=None)(self._resolve_cik_uncached)

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

    @lru_cache(maxsize=None)
    def _resolve_cik(self, ticker: str) -> int:
        """Resolves one ticker/CIK-string to its real int CIK --
        memoized via functools.lru_cache (keyed on (self, ticker), so
        each SEC instance gets its own cache) since a ticker's CIK is
        effectively permanent (SEC doesn't reassign one to a different
        company) and .financials()/.filings() are commonly called for
        the same ticker(s) repeatedly within one notebook session (income
        statement, then balance sheet, then cash flow) -- repeat calls
        skip the reference-table round trip entirely after the first.

        Binding the resolved int directly into the CALLER's own query --
        rather than calling sec_cik(...) inline against the big
        financials/facts/submissions scan -- is also what makes THAT
        scan's own cik filter pushdown-able at all. Confirmed live via
        EXPLAIN: a bound int gets pushed all the way into the Parquet
        scan as a real row-group-pruning filter ("Filters: cik=..."),
        while sec_cik(?) called inline forces a runtime join/subquery
        DuckDB can't push into the scan -- the difference between
        fetching a few matching row groups over the network and pulling
        every row of every file first, then filtering. lru_cache doesn't
        cache a call that raises, so a transient failure here (or a
        genuinely missing reference table) is never cached as if it
        were a resolved value."""
        try:
            row = self._connect().execute("SELECT sec_cik(?)", [ticker]).fetchone()
        except Exception as exc:
            if _is_missing_glob_error(exc, "reference"):
                raise RuntimeError(
                    "No sec/reference/company_tickers.parquet found -- nothing has been "
                    "backfilled yet, or --refresh-reference has never been run (see "
                    "scripts/backfill_sec_facts.py). Pass a raw CIK (an int, or an int-like "
                    "string) instead of a ticker to sidestep this lookup entirely."
                ) from exc
            raise
        return row[0]

    def _resolve_ciks(self, tickers: Sequence[Union[str, int]]) -> List[int]:
        """Resolves each of `tickers` (tickers or raw CIKs, mixed freely)
        to its real integer CIK -- see _resolve_cik()'s own docstring for
        the caching/pushdown rationale."""
        return [self._resolve_cik(str(t)) for t in tickers]

    def _execute(self, sql: str, params: List[Any], columns: List[str]) -> pd.DataFrame:
        """Runs `sql`/`params`, returning an EMPTY DataFrame (with the
        right `columns`) instead of raising when the underlying Parquet
        glob matches literally zero files -- a legitimate, expected state
        before this layer's first backfill, not an error. Any other
        DuckDB failure (a real network/credential/permission problem)
        still raises."""
        try:
            return self._connect().execute(sql, params).df()
        except Exception as exc:
            if _is_missing_glob_error(exc):
                return pd.DataFrame(columns=columns)
            raise

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
