# Symbol — one ergonomic object per ticker

*Full generated reference: [`tam.symbol`](api/tam.symbol.rst), [`tam.query`](api/tam.query.rst), [`tam.cache`](api/tam.cache.rst).*

The layer on top of raw SQL — one ticker (or several), one method per
dataset, mirroring the DuckDB macro names exactly so there's zero
translation cost between "I wrote raw SQL" and "I used the facade":

```python
from tam import Symbol

aapl = Symbol("AAPL")
aapl.minute_bars(start="2024-01-01")
aapl.daily_bars()
aapl.eod_bars()
aapl.splits()
aapl.dividends()
aapl.short_volume()
aapl.short_interest()
aapl.float_data()
aapl.financials(statement="income_statement")  # delegates to Sec; empty df if AAPL had no CIK
aapl.filings(forms=["10-K"])
```

Every method takes an optional `start=`/`end=` (where the dataset has a
real date column to filter on) and `engine="pandas"|"polars"` (DuckDB's
own native `.pl()` — no `tam` dependency on polars, install it yourself
to use this).

## Multiple tickers — same object, same methods

```python
basket = Symbol("AAPL", "MSFT", "NVDA")
basket.short_volume()  # ONE query -- scans every ticker's files at once, filtered `WHERE ticker IN (...)`
basket.minute_bars()  # one query PER ticker + a concat -- minute/eod files are per-symbol, there's no "every symbol" scan mode
```

Both shapes return one combined, long-format DataFrame with a
`ticker`/`symbol` column already in it — the same convention
`Sec.financials(tickers=[...])` already uses for multiple companies.

## Caching — opt-in, for when re-running a cell shouldn't re-fetch

```python
from tam import ManualCache

cache = ManualCache()  # construct once, reuse across cells
Symbol("AAPL", cache=cache).minute_bars()  # hits the connection
Symbol("AAPL", cache=cache).minute_bars()  # identical call -> cached, no re-fetch
cache.clear()  # explicit -- ManualCache never evicts on its own
```

Three implementations, pick by how long the cache should live:

| | Evicts | Use it for |
|---|---|---|
| `ManualCache()` | Only on `.clear()` | A notebook session — the default recommendation for Colab |
| `TTLCache(ttl_seconds=...)` | On read, once stale | A long-running process where the underlying data genuinely changes |
| `LRUCache(max_entries=...)` | Least-recently-read, once over capacity | Bounding memory across a long loop over many tickers |

`cache=` is accepted both on `Symbol(...)` (the default for every method
call on that instance) and on an individual method call (overrides just
that call). Omit it entirely (the default) to never cache — identical
behavior to before caching existed. Every implementation keys on the
exact `(sql, params, engine)` tuple that would otherwise run, so
correctness is automatic regardless of which method produced the query.

## `tam.query()` — raw SQL, no ticker object needed

```python
import tam

tam.query("SELECT * FROM daily_bars('AAPL') ORDER BY day")
tam.query("SELECT count(*) FROM sec_companies()", cache=tam.ManualCache())
```

The lower-level tier `Symbol` itself is built on — for a cross-ticker
join or a whole-universe aggregation that doesn't fit a single ticker or
a fixed list of them.

## Connections

Omit every connection kwarg (the common case) and `Symbol`/`tam.query()`/
`Sec`'s own shared default instance all reuse ONE lazily-built default
connection for the whole process — constructing ten `Symbol(...)`
instances in a notebook doesn't mint ten separate R2 credentials. Override
per-instance with `con=` (an existing connection, e.g. from
`tam.marketdata.explorer_client.connect()`), `local_root=...` (a local
Parquet tree), or any other `open_duckdb()` kwarg (e.g. `bucket=...` for a
different R2 bucket) — see [Architecture & background](architecture.md#r2-setup)
for the full credential-resolution chain.

## Extending — adding a new dataset

The dozen or so methods that just take a ticker + an optional date range
(`splits`, `short_volume`, `minute_bars`, ...) are generated from one
declarative registry, `tam.marketdata.datasets.DATASETS` — adding a new
one that fits this shape is one new `DatasetSpec` entry plus one short
method on `Symbol`, not new query-building logic. A macro that takes an
extra argument (`rollup_bars`, `rolling_volatility`) or needs its own
resolution logic (`financials`/`filings`, via `Sec`) gets its own
hand-written method instead — see `tam/symbol.py`'s own module docstring
for the reasoning.
