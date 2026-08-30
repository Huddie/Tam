# Market data

*Full generated reference: [`tam.marketdata`](api/tam.marketdata.rst).*

A ~20-year historical dataset of 1-minute OHLCV bars for SPY and the
point-in-time S&P 500 universe (no survivorship bias), plus corporate
actions and positioning data (splits, dividends, IPOs, short volume,
short interest, float) for every US-listed ticker — all stored as Parquet
in Cloudflare R2 and queried directly with DuckDB. Daily/weekly/monthly/
N-minute rollups, returns, and volatility are **derived on demand** from
the 1-minute data at query time, not precomputed.

This is a separate concern from `tam.data`'s own end-of-day cache (used by
backtests) — see [Data](data.md).

For *how* this gets ingested/backfilled and *why* it's laid out this way,
see [Architecture & background](architecture.md#market-data-minute-bars-reference-data).
For the exact on-disk paths/schemas, see [Data storage layout](storage-layout.md#equity-market-data).

```bash
pip install "tam-quant[marketdata]"
```

## Querying

```python
from tam.marketdata.duckdb_query import open_duckdb

con = open_duckdb(bucket="tam-data")  # reads from R2 directly
# con = open_duckdb(local_root="data/minute")  # or: plain local Parquet, no R2/network

con.sql("SELECT * FROM minute_bars('SPY') WHERE ts >= '2020-03-01' ORDER BY ts").df()
con.sql("SELECT * FROM daily_bars('SPY') ORDER BY day").df()
con.sql("SELECT * FROM weekly_bars('SPY') ORDER BY week").df()
con.sql("SELECT * FROM monthly_bars('SPY') ORDER BY month").df()
con.sql("SELECT * FROM rollup_bars('SPY', 5) ORDER BY bucket").df()  # any N-minute bars
con.sql("SELECT * FROM daily_returns('SPY') ORDER BY day").df()
con.sql("SELECT * FROM rolling_volatility('SPY', 21) ORDER BY day").df()  # 21-day annualized vol
con.sql("SELECT * FROM eod_bars('AAPL') ORDER BY date").df()  # true EOD (tam.data's lake), adj_close included
```

Every one of these is a DuckDB SQL macro over the raw 1-minute Parquet
files — nothing is precomputed or stored ahead of time; add your own macro
(or just write ad hoc SQL against `minute_bars(...)`/`read_parquet(...)`
directly) for any other rollup or research feature you need. `open_duckdb()`
also registers the [SEC macros](research-sec.md#querying-with-raw-sql) —
one connection covers all lakes.

### `MinuteBarStore` — the Python API, not raw SQL

```python
from tam.marketdata.store import R2MinuteBarStore  # or LocalMinuteBarStore(root)

store = R2MinuteBarStore()
df = store.read("SPY")  # full history for one symbol
store.write("SPY", df)  # upsert -- merges into whatever year(s) df's index spans
store.exists("SPY")
store.list_symbols()  # every symbol currently in the bucket
```

### From a personal token (no admin R2 credentials needed)

Recommended for Colab or a laptop — see [Notebooks](notebooks.md#querying-the-market-data-lakes)
for the full self-service token flow via `tam.marketdata.explorer_client.connect()`.

## Reference data

Same vendor as the minute bars above (Massive, formerly Polygon.io), same
`open_duckdb()`/DuckDB-macro pattern — no Python wrapper class needed for
querying.

```bash
pip install "tam-quant[marketdata]"   # same extra as minute bars -- adds the `massive` SDK
```

```python
from tam.marketdata.duckdb_query import open_duckdb

con = open_duckdb(bucket="tam-data")

con.sql("SELECT * FROM splits('AAPL') ORDER BY execution_date").df()
con.sql("SELECT * FROM dividends('AAPL') ORDER BY ex_dividend_date").df()
con.sql("SELECT * FROM ipos() ORDER BY listing_date DESC").df()  # every ticker, no arg needed
con.sql("SELECT * FROM short_volume('AAPL') ORDER BY date").df()
con.sql("SELECT * FROM short_interest('AAPL') ORDER BY settlement_date").df()
con.sql("SELECT * FROM float_data('AAPL')").df()
```

Every macro takes an **optional** ticker (`splits()` with no argument
returns every ticker's rows at once) — pass one to scope to a single
company.

### `ReferenceStore` — the Python API, not raw SQL

```python
from tam.marketdata.reference_store import R2ReferenceStore  # or LocalReferenceStore(root)
from tam.marketdata.reference_provider import MassiveReferenceProvider

store = R2ReferenceStore()
df = store.read("splits")  # every ticker
df = store.read("short_volume", ticker="AAPL")  # per-ticker datasets accept an optional ticker=

provider = MassiveReferenceProvider()  # needs MASSIVE_API_KEY
fresh = provider.fetch_splits(since="2024-01-01")
```

Dataset names: `"splits"`, `"dividends"`, `"ipos"`, `"short_volume"`,
`"short_interest"`, `"float"`. See
[Data storage layout](storage-layout.md#equity-market-data) for exact
columns and on-disk paths per dataset.

## R2 credentials

Read-only credentials are enough to query either lake — see
[Architecture & background](architecture.md#r2-setup) for the full setup
(bucket creation, token scopes, credential resolution order) and for how
these lakes actually get populated (backfills, daily ingestion GitHub
Actions).
