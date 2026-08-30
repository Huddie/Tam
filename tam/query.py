"""tam.query() -- raw SQL over every macro at once, no ticker object
needed. The low-level tier of tam's data-access layer: `tam.Symbol`/
`tam.Symbols` are the ergonomic per-ticker layer built on top of exactly
this; reach for `query()` directly for anything that doesn't fit a single
ticker or a fixed list of them (a cross-ticker join, an aggregation over
the whole universe, ...).

    import tam

    tam.query("SELECT * FROM daily_bars('AAPL') ORDER BY day")
    tam.query("SELECT * FROM splits() WHERE execution_date >= '2024-01-01'", engine="polars")

    cache = tam.ManualCache()
    tam.query("SELECT count(*) FROM sec_companies()", cache=cache)   # hits the connection
    tam.query("SELECT count(*) FROM sec_companies()", cache=cache)   # identical call -> cached
"""

from __future__ import annotations

from typing import Any, Optional

from .cache import Cache
from .marketdata.connection import default_connection, resolve_connection


def query(
    sql: str,
    *,
    engine: str = "pandas",
    cache: Optional[Cache] = None,
    con: Any = None,
    **connection_kwargs: Any,
) -> Any:
    """Runs `sql` and returns pandas (`engine="pandas"`, the default) or
    polars (`engine="polars"`, DuckDB's own native `.pl()` -- no `tam`
    dependency on polars itself, install it yourself if you want this).
    `con=`/`local_root=`/any other `open_duckdb()` kwarg overrides the
    connection outright (same resolution chain as `tam.Symbol`/`Sec`);
    omit all of them to share the one lazily-built default connection
    every default-configured caller in the process reuses. `cache=` (a
    `tam.Cache`) is opt-in, keyed on the exact `(sql, engine)` pair."""
    if con is not None:
        active_con = con
    elif connection_kwargs:
        active_con = resolve_connection(**connection_kwargs)
    else:
        active_con = default_connection()

    key = (sql, engine)
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached

    relation = active_con.sql(sql)
    result = relation.pl() if engine == "polars" else relation.df()

    if cache is not None:
        cache.set(key, result)
    return result
