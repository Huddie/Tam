"""tam.research.data.sec -- SEC XBRL/filings data lake: R2 is the durable
cache, SEC's own JSON APIs are the source of truth, EdgarTools' concept-
standardization table does the raw-concept-name-to-line-item mapping (see
normalize.py's own docstring for why, and why NOT its heavier per-filing
XBRL-parsing path for the raw facts themselves).

    from tam.research.data.sec import Sec

    Sec.companies(search="apple")                           # find a ticker/CIK -- ["AAPL", 320193, "Apple Inc."]
    Sec.statements()                                        # valid statement= values
    Sec.line_items(tickers=["AAPL"], search="rev")          # valid line_items= values for this company
    Sec.financials(tickers=["AAPL", "MSFT"], start=2015)    # no construction needed -- a shared default instance
    Sec.forms(tickers=["AAPL"])                             # valid forms= values for this company
    Sec.filings(ticker="AAPL", forms=["10-K", "10-Q"], start="2015-01-01")
    Sec.query("SELECT cik, fiscal_year, value FROM sec_stmt('income_statement') WHERE line_item = 'revenue'")

    sec = Sec(local_root="data")                   # reads local Parquet instead -- no network
    sec = Sec(bucket="tam-data")                   # raw R2 account credentials instead of a personal token
    sec.financials(...)                            # this instance's OWN connection, separate from the shared default

Every parameter a caller must pick a value for has a matching discovery
method returning the real, legal options as a dataframe: `companies()`
for `tickers=`/`ticker=`, `statements()` for `statement=`, `line_items()`
for `line_items=` (plus `line_item_catalog()` for the full theoretical
catalog and `concepts()` for raw-tag traceability), `forms()` for
`forms=`. None of these guess -- `companies()`/`line_items()`/`concepts()`/
`forms()` query the real ingested data; `statements()`/`line_item_catalog()`
are local lookups over the same concept table `normalize_facts()` itself
uses.

Thin wrappers over the SQL macros tam.marketdata.duckdb_query.open_duckdb()
registers (sec_facts/sec_financials/sec_stmt/sec_filings/sec_companies/
sec_cik) -- see that module's own docstring for the full macro set and how
ticker-or-CIK resolution works. Not exposed as `tam.Sec` at the top level (unlike
`tam.Secrets`/`tam.Fred`) -- normalize.py imports edgartools' concept-
standardization table eagerly, a heavier dependency than fredapi, so
`import tam` itself should stay cheap; use this explicit submodule import
instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache, update_wrapper
from typing import Any

import pandas as pd

from . import schema
from .manifest import Manifest
from .normalize import _synonyms, normalize_facts
from .provider import SecProvider
from .store import SecStore

__all__ = ["Manifest", "Sec", "SecProvider", "SecStore", "normalize_facts"]


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


class _default_or_bound:
    """Descriptor: makes an instance method ALSO directly callable on the
    class itself -- `Sec.financials(...)` -- via a lazily-created, shared
    default Sec() instance, the same "call it directly on the name, no
    construction needed" ergonomic tam.Fred/tam.Secrets already give
    (Fred.get(...), Secrets.get(...)). Sec can't just BE a pre-built
    singleton the way those are, though: constructing one with a specific
    local_root=/bucket=/token= is a real, common need this class already
    supports, so `sec = Sec(local_root=...); sec.financials(...)` must
    keep working exactly like a normal bound method, using THAT
    instance's own separate connection/config -- never the shared
    default. This gives both instead of picking one.

    `instance is None` is exactly how Python signals "accessed on the
    class, not an instance" for any descriptor -- `Sec.financials` triggers
    this with instance=None; `sec.financials` (a real Sec object) passes
    the real instance through unchanged, so per-instance behavior is
    completely untouched."""

    def __init__(self, func):
        self._func = func
        update_wrapper(self, func)  # keep the real method's __name__/__doc__ for help()/introspection

    def __get__(self, instance, owner):
        if instance is None:
            instance = owner._default()
        return self._func.__get__(instance, owner)


class Sec:
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
    notebook. `Sec` never writes anything (every method here is a SELECT;
    ingestion is scripts/backfill_sec_facts.py's/SecStore's job, using
    real R2 admin credentials, a completely separate concern) -- so a
    read-only token is exactly the right amount of access, not a
    limitation to work around. Raises a clear, actionable error if
    NEITHER an explicit override nor a token resolves to anything."""

    def __init__(
        self,
        *,
        token: str | None = None,
        api_url: str | None = None,
        ttl_seconds: int | None = None,
        local_root: str | None = None,
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
        # across every Sec instance ever created, keyed on (self, ticker),
        # which keeps every one of those instances (and its DuckDB
        # connection) alive for the rest of the process. Binding a fresh
        # lru_cache to this instance's own bound method instead means the
        # cache -- and the `self` reference inside it -- is freed the
        # moment this Sec instance is.
        self._resolve_cik = lru_cache(maxsize=None)(self._resolve_cik_uncached)

    _shared_default: Sec | None = None

    @classmethod
    def _default(cls) -> Sec:
        """The shared instance `Sec.financials(...)`/`Sec.filings(...)`/
        `Sec.query(...)` use when called directly on the class -- built
        once, on first such use, then reused (same "connect once, cache,
        reuse" contract any Sec instance already gives you, not a fresh
        connection per call). Uses the plain default resolution chain
        (TAM_PAT token, same as `Sec()` with no arguments) -- construct
        your own instance instead (`Sec(local_root=...)`, `Sec(bucket=...)`,
        `Sec(token=...)`) for anything else; that instance's own
        connection is completely separate from this shared one."""
        if cls._shared_default is None:
            cls._shared_default = cls()
        return cls._shared_default

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

    def _resolve_cik_uncached(self, ticker: str) -> int:
        """Resolves one ticker/CIK-string to its real int CIK. Wrapped by
        __init__ in a PER-INSTANCE functools.lru_cache (see __init__'s
        own comment on why per-instance, not a class-level decorator)
        since a ticker's CIK is effectively permanent (SEC doesn't
        reassign one to a different company) and .financials()/
        .filings() are commonly called for the same ticker(s) repeatedly
        within one notebook session (income statement, then balance
        sheet, then cash flow) -- repeat calls skip the reference-table
        round trip entirely after the first.

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

    def _resolve_ciks(self, tickers: Sequence[str | int]) -> list[int]:
        """Resolves each of `tickers` (tickers or raw CIKs, mixed freely)
        to its real integer CIK -- see _resolve_cik_uncached()'s own docstring for
        the caching/pushdown rationale."""
        return [self._resolve_cik(str(t)) for t in tickers]

    def _execute(self, sql: str, params: list[Any], columns: list[str]) -> pd.DataFrame:
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

    @_default_or_bound
    def query(self, sql: str) -> pd.DataFrame:
        """Raw SQL access to every sec_* macro (and minute_bars/eod_bars,
        since it's the SAME connection) -- `sec.query("SELECT cik,
        fiscal_year, value FROM sec_stmt('income_statement') WHERE
        line_item = 'revenue'")`."""
        return self._connect().sql(sql).df()

    @_default_or_bound
    def companies(self, search: str | None = None) -> pd.DataFrame:
        """Every ticker/CIK/company name on record -- the reference table
        every other method's `tickers=`/`ticker=` values come from.
        `search` matches (case-insensitively) as a substring of EITHER the
        ticker or the company name; omit it to list every company."""
        where = ""
        params: list[Any] = []
        if search:
            where = "WHERE ticker ILIKE ? OR entity_name ILIKE ?"
            pattern = f"%{search}%"
            params = [pattern, pattern]
        sql = f"""
            SELECT cik, ticker, entity_name
            FROM sec_companies()
            {where}
            ORDER BY ticker
        """
        return self._execute(sql, params, schema.REFERENCE_COLUMNS)

    @_default_or_bound
    def statements(self) -> pd.DataFrame:
        """The fixed, small set of valid `statement=` values accepted by
        `financials()`/`line_items()`/`line_item_catalog()` -- a pure local
        lookup (no query, no network) over the same concept-standardization
        table `normalize_facts()` itself uses to categorize every concept,
        not a separately-maintained list that could drift out of sync."""
        values = sorted({_synonyms.get_group(name).category for name in _synonyms.list_groups()})
        return pd.DataFrame({schema.STATEMENT: values})

    @_default_or_bound
    def line_item_catalog(self, statement: str | None = None) -> pd.DataFrame:
        """Every line-item name `normalize_facts()` knows how to produce,
        independent of whether any ingested company actually has data for
        it yet -- the full theoretical catalog, optionally filtered to one
        `statement` (see `Sec.statements()` for valid values). Pure local
        lookup, no query, no network -- the "browse everything we know how
        to normalize" companion to `line_items()`'s "what does THIS
        company actually have"."""
        rows = [
            {schema.LINE_ITEM: name, schema.STATEMENT: _synonyms.get_group(name).category}
            for name in _synonyms.list_groups()
        ]
        catalog = pd.DataFrame(rows, columns=[schema.LINE_ITEM, schema.STATEMENT])
        if statement:
            catalog = catalog[catalog[schema.STATEMENT] == statement]
        return catalog.sort_values(schema.LINE_ITEM).reset_index(drop=True)

    @_default_or_bound
    def line_items(
        self,
        tickers: Sequence[str | int] | None = None,
        search: str | None = None,
        statement: str | None = None,
    ) -> pd.DataFrame:
        """Which line items actually have data for `tickers` (or every
        company on record, if omitted), ranked by how well-populated each
        one is -- answers "what can I actually pass to
        financials(line_items=[...])" from real ingested data, not a guess
        from the full theoretical catalog (`line_item_catalog()` is that).
        `search` narrows by a case-insensitive substring of the line item
        name; `statement` narrows to one statement (see `Sec.statements()`
        for valid values). The `concepts` column shows which raw XBRL
        concept(s) actually rolled up into each line item -- pass one of
        them, or the line item itself, to `Sec.concepts()` for the
        reverse, per-company breakdown."""
        where: list[str] = []
        params: list[Any] = []

        if tickers:
            ciks = self._resolve_ciks(tickers)
            placeholders = ", ".join("?" for _ in ciks)
            where.append(f"cik IN ({placeholders})")
            params.extend(ciks)
        if search:
            where.append("line_item ILIKE ?")
            params.append(f"%{search}%")
        if statement:
            where.append("statement = ?")
            params.append(statement)

        clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"""
            SELECT statement, line_item,
                   list(DISTINCT concept) AS concepts,
                   count(*) AS fact_count
            FROM sec_financials()
            {clause}
            GROUP BY statement, line_item
            ORDER BY fact_count DESC
        """
        return self._execute(sql, params, [schema.STATEMENT, schema.LINE_ITEM, "concepts", "fact_count"])

    @_default_or_bound
    def concepts(self, line_item: str, tickers: Sequence[str | int] | None = None) -> pd.DataFrame:
        """Which raw XBRL concepts actually rolled up into `line_item`
        (see `Sec.line_items()` for valid values), and for which
        companies -- the reverse lookup from `line_items()`'s own
        `concepts` column, for when you already know the line item and
        want to trace it back to the raw tag(s)."""
        where = ["line_item = ?"]
        params: list[Any] = [line_item]
        if tickers:
            ciks = self._resolve_ciks(tickers)
            placeholders = ", ".join("?" for _ in ciks)
            where.append(f"cik IN ({placeholders})")
            params.extend(ciks)
        sql = f"""
            SELECT DISTINCT cik, concept
            FROM sec_financials()
            WHERE {" AND ".join(where)}
            ORDER BY cik, concept
        """
        return self._execute(sql, params, [schema.CIK, schema.CONCEPT])

    @_default_or_bound
    def financials(
        self,
        tickers: Sequence[str | int] | None = None,
        statement: str | None = None,
        line_items: Sequence[str] | None = None,
        start: str | int | None = None,
        end: str | int | None = None,
        dedupe_periods: bool = True,
    ) -> pd.DataFrame:
        """Normalized financials (long format: one row per line item), for
        any combination of `tickers` (tickers or raw CIKs, mixed freely --
        see `Sec.companies()` to find one), `statement` (see
        `Sec.statements()` for valid values), `line_items` (see
        `Sec.line_items()` for what this company/these companies actually
        report), and a `fiscal_year` range via `start`/`end`. Omitting all
        of them returns every company/period on record.

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
        where: list[str] = []
        params: list[Any] = []

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

        return self._execute(sql, params, schema.FINANCIALS_COLUMNS)

    @_default_or_bound
    def filings(
        self,
        ticker: str | int | None = None,
        forms: Sequence[str] | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> pd.DataFrame:
        """Filing metadata (accession number, form, filed date, period of
        report, ...) for one company, optionally scoped to specific
        `forms` (see `Sec.forms()` for valid values) and a filed-date
        range. `filed_date`/`period_of_report` come back as real dates
        (cast in SQL), rows pre-sorted chronologically."""
        where: list[str] = []
        params: list[Any] = []

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
        return self._execute(sql, params, schema.SUBMISSIONS_COLUMNS)

    @_default_or_bound
    def forms(self, tickers: Sequence[str | int] | None = None) -> pd.DataFrame:
        """Which filing forms actually have data for `tickers` (or every
        company on record, if omitted), ranked by count -- answers "what
        can I actually pass to filings(forms=[...])" from real ingested
        data, not a guess at SEC's own form-type list."""
        where: list[str] = []
        params: list[Any] = []
        if tickers:
            ciks = self._resolve_ciks(tickers)
            placeholders = ", ".join("?" for _ in ciks)
            where.append(f"cik IN ({placeholders})")
            params.extend(ciks)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"""
            SELECT form, count(*) AS filing_count
            FROM sec_filings()
            {clause}
            GROUP BY form
            ORDER BY filing_count DESC
        """
        return self._execute(sql, params, [schema.FORM, "filing_count"])
