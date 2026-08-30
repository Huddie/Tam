"""tam.Symbol -- one object per ticker (or several at once), backed by
whatever connection tam.marketdata.connection.resolve_connection() would
give you: every method mirrors a DuckDB macro name exactly
(`.minute_bars()`, `.splits()`, `.short_volume()`, ...) and returns
either pandas or polars (`engine=`, see `tam.Engine`), optionally through
a tam.Cache you provide (`cache=`) so re-running the same call -- the
common case in a notebook, re-running a cell -- doesn't re-hit the
connection::

    from tam import Symbol, CIK

    aapl = Symbol("AAPL")
    aapl.minute_bars(start="2024-01-01")
    aapl.splits()
    aapl.financials(statement="income_statement")   # delegates to Sec; empty df if AAPL had no CIK

    Symbol(CIK(320193)).splits()                      # same AAPL, identified by its SEC CIK instead

    basket = Symbol("AAPL", "MSFT", "NVDA")           # same methods, same shapes, just more tickers
    basket.short_volume()                             # ONE query (scan-all + WHERE ticker IN (...)), not three

    from tam import ManualCache
    cache = ManualCache()                             # construct once, reuse across cells
    Symbol("AAPL", cache=cache).minute_bars()          # hits the connection
    Symbol("AAPL", cache=cache).minute_bars()          # identical call -> cached

    aapl.splits(columns=["ticker", "execution_date"])  # only these columns, not every one

For raw SQL with no ticker object at all, see `tam.query()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Union

from .cache import Cache
from .engine import Engine
from .marketdata.connection import default_connection, is_missing_glob_error, resolve_connection
from .marketdata.datasets import DATASETS, DatasetSpec

_UNSET = object()


@dataclass(frozen=True)
class CIK:
    """Wraps a raw SEC CIK (an int, or an int-like string) to disambiguate
    it from a ticker string when constructing a Symbol -- `Symbol(CIK(320193))`
    is unambiguous even though a bare int could otherwise be misread as
    something else. Mixes freely with plain ticker strings
    (`Symbol("MSFT", CIK(320193))`).

    Resolved automatically wherever it's used: to AAPL's real ticker (via
    `sec_companies()`, cached after the first lookup) for the ticker-column
    macros (`minute_bars`, `splits`, `short_volume`, ...) -- those have no
    CIK concept at all, only a `ticker` column -- and passed straight
    through to `tam.research.data.sec.Sec` (which already accepts a
    ticker OR a raw CIK interchangeably) for `.financials()`/`.filings()`,
    with no resolution needed there at all."""

    value: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", int(self.value))

    def __repr__(self) -> str:
        return f"CIK({self.value})"


Tickerable = Union[str, CIK]


def _select_columns(requested: Optional[Sequence[str]], valid: Sequence[str]) -> Optional[List[str]]:
    if requested is None:
        return None
    invalid = [c for c in requested if c not in valid]
    if invalid:
        raise ValueError(f"Unknown column(s) {invalid} -- valid columns are: {list(valid)}")
    return list(requested)


def _empty_frame(columns: List[str], engine: str):
    if engine == Engine.POLARS:
        import polars as pl

        return pl.DataFrame(schema=columns)
    import pandas as pd

    return pd.DataFrame(columns=columns)


def _concat(frames: list, engine: str):
    if len(frames) == 1:
        return frames[0]
    if engine == Engine.POLARS:
        import polars as pl

        return pl.concat(frames)
    import pandas as pd

    return pd.concat(frames, ignore_index=True)


def _project(frame: Any, columns: Optional[Sequence[str]], engine: str):
    if columns is None:
        return frame
    if engine == Engine.POLARS:
        return frame.select(list(columns))
    return frame[list(columns)]


class Symbol:
    """`Symbol("AAPL")` or `Symbol("AAPL", "MSFT", ...)` -- every method
    below works identically either way; see each method's own docstring
    for how the multi-ticker case is actually executed (one combined
    query for datasets that support it, one query per ticker + a concat
    for the ones that don't -- minute/eod/rollup macros require a real
    ticker, they have no "every symbol" scan mode). Accepts `CIK(...)`
    anywhere a ticker string works instead (see `CIK`'s own docstring) --
    `Symbol("MSFT", CIK(320193))` mixes both freely.

    `con=`/`local_root=`/any other `tam.marketdata.duckdb_query.
    open_duckdb()` kwarg overrides the connection outright, same
    resolution chain as `tam.research.data.sec.Sec` and `tam.query()` --
    omit all of them (the common case) to share the one lazily-built
    default connection every default-configured caller in the process
    reuses (see `tam.marketdata.connection.default_connection()`).

    `cache=` (a `tam.Cache`, e.g. `tam.ManualCache()`) is the DEFAULT for
    every method call on this instance -- pass `cache=` again to an
    individual method call to override just that one call; omit both (the
    default) to never cache at all, exactly the pre-caching behavior.

    `columns=` (any method) selects a subset instead of every column --
    validated against that dataset's own known columns (a typo raises
    `ValueError` listing the real ones, rather than building broken SQL --
    column names can't be bound as query parameters the way values can)."""

    def __init__(
        self,
        *tickers: Tickerable,
        con: Any = None,
        cache: Optional[Cache] = None,
        **connection_kwargs: Any,
    ):
        if not tickers:
            raise ValueError("Symbol(...) needs at least one ticker")
        for t in tickers:
            if not isinstance(t, (str, CIK)):
                raise TypeError(f"Symbol(...) accepts a ticker string or CIK(...), got {t!r} ({type(t).__name__})")
        self.tickers: List[Tickerable] = [t.upper() if isinstance(t, str) else t for t in tickers]
        self._con = con
        self._connection_kwargs = connection_kwargs
        self._default_cache = cache
        self._resolved_bar_tickers: Optional[List[str]] = None

    def __repr__(self) -> str:
        return f"Symbol({', '.join(repr(t) for t in self.tickers)})"

    def _connect(self):
        if self._con is None:
            self._con = (
                resolve_connection(**self._connection_kwargs) if self._connection_kwargs else default_connection()
            )
        return self._con

    def _resolve_cik_to_ticker(self, cik: int) -> str:
        row = self._connect().execute("SELECT ticker FROM sec_companies() WHERE cik = ?", [cik]).fetchone()
        if row is None or row[0] is None:
            raise RuntimeError(
                f"No ticker on record for CIK {cik} (sec_companies() has no matching row) -- "
                "bars/reference-data macros are keyed by ticker, not CIK, so this can't be "
                "resolved automatically. Pass the real ticker string directly instead."
            )
        return row[0]

    def _bar_tickers(self) -> List[str]:
        """Every ticker this Symbol represents, as real ticker STRINGS --
        resolves any `CIK(...)` entries via `sec_companies()` (once,
        cached for the life of this instance) since the ticker-column
        macros have no CIK concept at all. Used by every method EXCEPT
        `.financials()`/`.filings()`, which pass CIKs straight through to
        `Sec` instead (see `_sec_tickers()`)."""
        if self._resolved_bar_tickers is None:
            self._resolved_bar_tickers = [
                self._resolve_cik_to_ticker(t.value) if isinstance(t, CIK) else t for t in self.tickers
            ]
        return self._resolved_bar_tickers

    def _sec_tickers(self) -> List[Union[str, int]]:
        """Every ticker/CIK this Symbol represents, in the shape
        `Sec.financials()`/`.filings()` already accept directly (a ticker
        string OR a raw CIK int) -- no resolution needed here, `Sec` does
        its own ticker<->CIK lookup internally."""
        return [t.value if isinstance(t, CIK) else t for t in self.tickers]

    def _resolve_cache(self, cache: Any) -> Optional[Cache]:
        return self._default_cache if cache is _UNSET else cache

    def _run(self, sql: str, params: Sequence[Any], columns: List[str], *, engine: str, cache: Any) -> Any:
        resolved_cache = self._resolve_cache(cache)
        key = (sql, tuple(params), engine)
        if resolved_cache is not None:
            cached = resolved_cache.get(key)
            if cached is not None:
                return cached
        try:
            relation = self._connect().execute(sql, list(params))
            result = relation.pl() if engine == Engine.POLARS else relation.df()
        except Exception as exc:
            if not is_missing_glob_error(exc):
                raise
            result = _empty_frame(columns, engine)
        if resolved_cache is not None:
            resolved_cache.set(key, result)
        return result

    def query(self, sql: str, *, engine: Union[str, Engine] = Engine.PANDAS, cache: Any = _UNSET) -> Any:
        """Raw SQL over this Symbol's own connection -- every macro is
        available, not just the ones with a dedicated method below (e.g.
        `rollup_bars`/`rolling_volatility`, which take an extra argument
        their macro-mirroring method already exposes, or anything you'd
        register yourself). `cache=`/`engine=` behave exactly like every
        other method here."""
        resolved_cache = self._resolve_cache(cache)
        key = (sql, (), engine)
        if resolved_cache is not None:
            cached = resolved_cache.get(key)
            if cached is not None:
                return cached
        relation = self._connect().sql(sql)
        result = relation.pl() if engine == Engine.POLARS else relation.df()
        if resolved_cache is not None:
            resolved_cache.set(key, result)
        return result

    def _fetch(
        self,
        spec: DatasetSpec,
        *,
        start: Any = None,
        end: Any = None,
        columns: Optional[Sequence[str]] = None,
        engine: str = Engine.PANDAS,
        cache: Any = _UNSET,
    ) -> Any:
        selected = _select_columns(columns, spec.columns)
        select_clause = ", ".join(selected) if selected else "*"
        tickers = self._bar_tickers()
        if spec.supports_scan_all and len(tickers) > 1:
            conditions = [f"{spec.ticker_column} IN ({', '.join('?' for _ in tickers)})"]
            params: List[Any] = list(tickers)
            if spec.date_column:
                if start is not None:
                    conditions.append(f"{spec.date_column} >= ?")
                    params.append(str(start))
                if end is not None:
                    conditions.append(f"{spec.date_column} <= ?")
                    params.append(str(end))
            order = (
                f" ORDER BY {spec.ticker_column}, {spec.date_column}"
                if spec.date_column
                else f" ORDER BY {spec.ticker_column}"
            )
            sql = f"SELECT {select_clause} FROM {spec.macro}() WHERE {' AND '.join(conditions)}{order}"
            return self._run(sql, params, selected or spec.columns, engine=engine, cache=cache)

        frames = []
        for ticker in tickers:
            conditions = []
            params = [ticker]
            if spec.date_column:
                if start is not None:
                    conditions.append(f"{spec.date_column} >= ?")
                    params.append(str(start))
                if end is not None:
                    conditions.append(f"{spec.date_column} <= ?")
                    params.append(str(end))
            where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
            order = f" ORDER BY {spec.date_column}" if spec.date_column else ""
            sql = f"SELECT {select_clause} FROM {spec.macro}(?){where}{order}"
            frames.append(self._run(sql, params, selected or spec.columns, engine=engine, cache=cache))
        return _concat(frames, engine)

    # ---- macro-mirroring methods, spec-driven -------------------------------

    def minute_bars(
        self,
        start: Any = None,
        end: Any = None,
        *,
        columns: Optional[Sequence[str]] = None,
        engine: Union[str, Engine] = Engine.PANDAS,
        cache: Any = _UNSET,
    ) -> Any:
        return self._fetch(DATASETS["minute_bars"], start=start, end=end, columns=columns, engine=engine, cache=cache)

    def daily_bars(
        self,
        start: Any = None,
        end: Any = None,
        *,
        columns: Optional[Sequence[str]] = None,
        engine: Union[str, Engine] = Engine.PANDAS,
        cache: Any = _UNSET,
    ) -> Any:
        return self._fetch(DATASETS["daily_bars"], start=start, end=end, columns=columns, engine=engine, cache=cache)

    def weekly_bars(
        self,
        start: Any = None,
        end: Any = None,
        *,
        columns: Optional[Sequence[str]] = None,
        engine: Union[str, Engine] = Engine.PANDAS,
        cache: Any = _UNSET,
    ) -> Any:
        return self._fetch(DATASETS["weekly_bars"], start=start, end=end, columns=columns, engine=engine, cache=cache)

    def monthly_bars(
        self,
        start: Any = None,
        end: Any = None,
        *,
        columns: Optional[Sequence[str]] = None,
        engine: Union[str, Engine] = Engine.PANDAS,
        cache: Any = _UNSET,
    ) -> Any:
        return self._fetch(DATASETS["monthly_bars"], start=start, end=end, columns=columns, engine=engine, cache=cache)

    def eod_bars(
        self,
        start: Any = None,
        end: Any = None,
        *,
        columns: Optional[Sequence[str]] = None,
        engine: Union[str, Engine] = Engine.PANDAS,
        cache: Any = _UNSET,
    ) -> Any:
        return self._fetch(DATASETS["eod_bars"], start=start, end=end, columns=columns, engine=engine, cache=cache)

    def splits(
        self,
        start: Any = None,
        end: Any = None,
        *,
        columns: Optional[Sequence[str]] = None,
        engine: Union[str, Engine] = Engine.PANDAS,
        cache: Any = _UNSET,
    ) -> Any:
        return self._fetch(DATASETS["splits"], start=start, end=end, columns=columns, engine=engine, cache=cache)

    def dividends(
        self,
        start: Any = None,
        end: Any = None,
        *,
        columns: Optional[Sequence[str]] = None,
        engine: Union[str, Engine] = Engine.PANDAS,
        cache: Any = _UNSET,
    ) -> Any:
        return self._fetch(DATASETS["dividends"], start=start, end=end, columns=columns, engine=engine, cache=cache)

    def ipo(
        self,
        *,
        columns: Optional[Sequence[str]] = None,
        engine: Union[str, Engine] = Engine.PANDAS,
        cache: Any = _UNSET,
    ) -> Any:
        """Usually zero or one row per ticker -- an IPO listing, unlike
        everything else on Symbol, isn't an ongoing timeseries."""
        return self._fetch(DATASETS["ipo"], columns=columns, engine=engine, cache=cache)

    def short_volume(
        self,
        start: Any = None,
        end: Any = None,
        *,
        columns: Optional[Sequence[str]] = None,
        engine: Union[str, Engine] = Engine.PANDAS,
        cache: Any = _UNSET,
    ) -> Any:
        return self._fetch(DATASETS["short_volume"], start=start, end=end, columns=columns, engine=engine, cache=cache)

    def short_interest(
        self,
        start: Any = None,
        end: Any = None,
        *,
        columns: Optional[Sequence[str]] = None,
        engine: Union[str, Engine] = Engine.PANDAS,
        cache: Any = _UNSET,
    ) -> Any:
        return self._fetch(
            DATASETS["short_interest"], start=start, end=end, columns=columns, engine=engine, cache=cache
        )

    def float_data(
        self,
        *,
        columns: Optional[Sequence[str]] = None,
        engine: Union[str, Engine] = Engine.PANDAS,
        cache: Any = _UNSET,
    ) -> Any:
        return self._fetch(DATASETS["float_data"], columns=columns, engine=engine, cache=cache)

    # ---- extra-argument macros -- don't fit the DatasetSpec shape, own methods -----

    def rollup_bars(
        self,
        interval_minutes: int,
        *,
        columns: Optional[Sequence[str]] = None,
        engine: Union[str, Engine] = Engine.PANDAS,
        cache: Any = _UNSET,
    ) -> Any:
        all_columns = ["symbol", "bucket", "open", "high", "low", "close", "volume"]
        selected = _select_columns(columns, all_columns)
        select_clause = ", ".join(selected) if selected else "*"
        frames = []
        for ticker in self._bar_tickers():
            sql = f"SELECT {select_clause} FROM rollup_bars(?, ?) ORDER BY bucket"
            frames.append(
                self._run(sql, [ticker, interval_minutes], selected or all_columns, engine=engine, cache=cache)
            )
        return _concat(frames, engine)

    def daily_returns(
        self,
        *,
        columns: Optional[Sequence[str]] = None,
        engine: Union[str, Engine] = Engine.PANDAS,
        cache: Any = _UNSET,
    ) -> Any:
        all_columns = ["day", "close", "return"]
        selected = _select_columns(columns, all_columns)
        select_clause = ", ".join(selected) if selected else "*"
        frames = []
        for ticker in self._bar_tickers():
            sql = f"SELECT {select_clause} FROM daily_returns(?) ORDER BY day"
            frames.append(self._run(sql, [ticker], selected or all_columns, engine=engine, cache=cache))
        return _concat(frames, engine)

    def rolling_volatility(
        self,
        window_days: int,
        *,
        columns: Optional[Sequence[str]] = None,
        engine: Union[str, Engine] = Engine.PANDAS,
        cache: Any = _UNSET,
    ) -> Any:
        all_columns = ["day", "return", "annualized_vol"]
        selected = _select_columns(columns, all_columns)
        select_clause = ", ".join(selected) if selected else "*"
        frames = []
        for ticker in self._bar_tickers():
            sql = f"SELECT {select_clause} FROM rolling_volatility(?, ?) ORDER BY day"
            frames.append(self._run(sql, [ticker, window_days], selected or all_columns, engine=engine, cache=cache))
        return _concat(frames, engine)

    # ---- SEC -- delegates to Sec, doesn't reimplement CIK resolution/dedupe --------

    def financials(
        self,
        statement: Optional[str] = None,
        line_items: Optional[Sequence[str]] = None,
        start: Union[str, int, None] = None,
        end: Union[str, int, None] = None,
        dedupe_periods: bool = True,
        *,
        columns: Optional[Sequence[str]] = None,
        engine: Union[str, Engine] = Engine.PANDAS,
        cache: Any = _UNSET,
    ) -> Any:
        """Delegates to `tam.research.data.sec.Sec.financials()` (see its
        own docstring for `dedupe_periods`/every parameter's exact
        meaning) against THIS Symbol's own connection -- a ticker with no
        SEC CIK on record (ETFs, indices, foreign tickers) gets back an
        empty-but-correctly-shaped DataFrame, same as calling Sec
        directly. `engine="polars"` converts Sec's own pandas result
        afterward (Sec has no native polars path of its own) -- slightly
        less efficient than the other methods here, which query DuckDB's
        polars output directly, but still correct. `columns=` subsets
        the result afterward (Sec has no column-projection of its own
        either)."""
        from .research.data.sec import schema as sec_schema

        selected = _select_columns(columns, sec_schema.FINANCIALS_COLUMNS)
        resolved_cache = self._resolve_cache(cache)
        key = (
            "financials",
            tuple(self.tickers),
            statement,
            tuple(line_items) if line_items else None,
            start,
            end,
            dedupe_periods,
            tuple(selected) if selected else None,
            engine,
        )
        if resolved_cache is not None:
            cached = resolved_cache.get(key)
            if cached is not None:
                return cached
        from .research.data.sec import Sec

        result = Sec(con=self._connect()).financials(
            tickers=self._sec_tickers(),
            statement=statement,
            line_items=line_items,
            start=start,
            end=end,
            dedupe_periods=dedupe_periods,
        )
        if engine == Engine.POLARS:
            import polars as pl

            result = pl.from_pandas(result)
        result = _project(result, selected, engine)
        if resolved_cache is not None:
            resolved_cache.set(key, result)
        return result

    def filings(
        self,
        forms: Optional[Sequence[str]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        *,
        columns: Optional[Sequence[str]] = None,
        engine: Union[str, Engine] = Engine.PANDAS,
        cache: Any = _UNSET,
    ) -> Any:
        """Delegates to `Sec.filings()` per ticker (Sec's own `filings()`
        takes one ticker at a time; this loops and concatenates for a
        multi-ticker Symbol) and combines the result -- empty for a
        ticker with no SEC CIK on record. `columns=` subsets the result
        afterward, same as `.financials()`."""
        from .research.data.sec import schema as sec_schema

        selected = _select_columns(columns, sec_schema.SUBMISSIONS_COLUMNS)
        resolved_cache = self._resolve_cache(cache)
        key = (
            "filings",
            tuple(self.tickers),
            tuple(forms) if forms else None,
            start,
            end,
            tuple(selected) if selected else None,
            engine,
        )
        if resolved_cache is not None:
            cached = resolved_cache.get(key)
            if cached is not None:
                return cached
        from .research.data.sec import Sec

        sec = Sec(con=self._connect())
        frames = [sec.filings(ticker=ticker, forms=forms, start=start, end=end) for ticker in self._sec_tickers()]
        import pandas as pd

        result = frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)
        if engine == Engine.POLARS:
            import polars as pl

            result = pl.from_pandas(result)
        result = _project(result, selected, engine)
        if resolved_cache is not None:
            resolved_cache.set(key, result)
        return result
