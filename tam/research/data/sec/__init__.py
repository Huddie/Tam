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
            from ...marketdata.duckdb_query import open_duckdb

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
    ) -> pd.DataFrame:
        """Normalized financials (long format: one row per line item), for
        any combination of `tickers` (tickers or raw CIKs, mixed freely),
        `statement` ("income_statement"/"balance_sheet"/"cash_flow", ...),
        `line_items` ("revenue"/"net_income"/...), and a `fiscal_year`
        range via `start`/`end`. Omitting all of them returns every
        company/period on record."""
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
        return self._connect().execute(f"SELECT * FROM sec_financials() {clause}", params).df()

    def filings(
        self,
        ticker: Optional[Union[str, int]] = None,
        forms: Optional[Sequence[str]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """Filing metadata (accession number, form, filed date, period of
        report, ...) for one company, optionally scoped to specific
        `forms` and a filed-date range."""
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
        return self._connect().execute(f"SELECT * FROM sec_filings() {clause}", params).df()
