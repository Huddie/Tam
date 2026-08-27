"""tam.research.data.sec -- SEC XBRL/filings data lake: R2 is the durable
cache, SEC's own JSON APIs are the source of truth, EdgarTools' concept-
standardization table does the raw-concept-name-to-line-item mapping (see
normalize.py's own docstring for why, and why NOT its heavier per-filing
XBRL-parsing path for the raw facts themselves).

    from tam.research.data.sec import SEC

    sec = SEC()                                    # reads from R2 by default
    sec.financials(tickers=["AAPL", "MSFT"], start=2015)
    sec.filings(ticker="AAPL", forms=["10-K", "10-Q"], start="2015-01-01")
    sec.query("SELECT cik, fiscal_year, value FROM sec_stmt('income_statement') WHERE line_item = 'revenue'")

    sec = SEC(local_root="data")                   # reads local Parquet instead -- no network

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
    """Holds one lazily-created DuckDB connection (via
    tam.marketdata.duckdb_query.open_duckdb) and builds parameterized SQL
    against its sec_* macros -- every `tickers`/`forms`/`start`/`end`
    value is bound as a real DuckDB query parameter (`?`), never string-
    interpolated into the SQL text, so an odd ticker/form string can't
    corrupt or inject into the query."""

    def __init__(self, **open_duckdb_kwargs: Any):
        self._open_duckdb_kwargs = open_duckdb_kwargs
        self._con = None

    def _connect(self):
        if self._con is None:
            from ....marketdata.duckdb_query import open_duckdb

            self._con = open_duckdb(**self._open_duckdb_kwargs)
        return self._con

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
            placeholders = ", ".join("sec_cik(?)" for _ in tickers)
            where.append(f"cik IN ({placeholders})")
            params.extend(str(t) for t in tickers)
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
            where.append("cik = sec_cik(?)")
            params.append(str(ticker))
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
