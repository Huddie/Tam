# Market data

A ~20-year historical dataset of 1-minute OHLCV bars for SPY and the
point-in-time S&P 500 universe (no survivorship bias), stored as Parquet
in Cloudflare R2 and queried directly with DuckDB. Daily/weekly/monthly/
N-minute rollups, returns, and volatility are **derived on demand** from
the 1-minute data at query time, not precomputed.

This is a separate concern from `tam.data`'s own end-of-day cache (used by
backtests) — same conventions (the `Registry` pattern, symbol/year Parquet
partitioning), different code; see each module's own docstring for why.

```bash
pip install "tam-quant[marketdata]"
```

## Architecture at a glance

```
Massive flat files  -->  filter to universe  -->  validate  -->  R2 (Parquet)
  (MinuteBarProvider)   (tam.basket.universe)  (tam.marketdata.validate)  (MinuteBarStore)
```

- **`tam.marketdata.providers.MinuteBarProvider`** — fetches one vendor's
  full-market flat file for one day. `MassiveFlatFileProvider` ships built
  in; any other "one flat file per day" vendor needs no new code either,
  just `Registry.create(MinuteBarProvider, "flatfile_s3", endpoint=...,
  bucket=..., key_template=..., column_map={...}, ...)`.
- **`tam.basket.universe.UniverseProvider`** — reused as-is for
  point-in-time S&P 500 membership (see [Basket research](basket.md#universe-membership)).
- **`tam.marketdata.validate`** — OHLC integrity + trading-session-coverage
  checks, run before anything is written.
- **`tam.marketdata.store.MinuteBarStore`** — year-partitioned Parquet,
  `<root>/<SYMBOL>/<year>.parquet`, over any `pyarrow.fs.FileSystem` —
  `local_parquet` (plain disk) and `r2_parquet` (Cloudflare R2) are the
  same class, just pointed at a different filesystem. Every write also
  (re)computes a completeness sidecar (`<root>/<SYMBOL>/<year>.completeness.json`,
  actual-vs-expected trading-session minutes per day/month/year, from the
  NYSE calendar) — [tam-data-explorer](tam-data-explorer.md) reads this
  back verbatim to drive its completeness badge.
- **`tam.marketdata.ingest`** — wires the above into a resumable,
  idempotent backfill (a JSON manifest tracks which days are already done).
- **`tam.marketdata.duckdb_query.open_duckdb()`** — the query entry point;
  works identically from a local machine, Colab, or CI.

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
one connection covers all three lakes.

### From a personal token (no admin R2 credentials needed)

Recommended for Colab or a laptop — see [Notebooks](notebooks.md#querying-the-market-data-lakes)
for the full self-service token flow via `tam.marketdata.explorer_client.connect()`.

## R2 setup

1. Create an R2 bucket in the Cloudflare dashboard (e.g. `tam-data`).
2. Create **two** API tokens (R2 → Manage API Tokens): a **read-only** one
   for querying, and a **read-write** one for ingestion. Never use the
   write token from a notebook.
3. Set credentials — see `.env.example` for the exact names
   (`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
   `R2_BUCKET`). Resolution order
   (`tam.marketdata.credentials.resolve_r2_credentials`): explicit kwarg →
   env var → Colab secret (same name) → a saved file
   (`save_r2_credentials(...)`, written to `~/.config/tam-marketdata/r2_credentials.json`).
4. For Massive flat-file access, generate **S3-style** keys from your
   Massive dashboard (NOT a regular REST `apiKey`) and set
   `MASSIVE_S3_ACCESS_KEY_ID` / `MASSIVE_S3_SECRET_ACCESS_KEY`.

No boto3 dependency for this lake: reads/writes go through
`pyarrow.fs.S3FileSystem`; interactive querying goes through DuckDB's own
`httpfs` extension.

<details>
<summary>Backfilling / ingesting new data (maintainers only — not needed to query the lake)</summary>

Validate against a small range/universe locally first (no R2/Massive
credentials needed):

```bash
python -m examples.ingest_minute_bars examples/ingest_minute_bars_config.yaml
```

Then point the same CLI at real credentials for a production backfill —
swap `store: local_parquet` for `store: r2_parquet` and `universe: static`
for `universe: pitindex`. A large multi-year backfill is a one-off job —
run it locally or on a dedicated machine, not inside a GitHub Actions job
(runtime-limited); re-running after an interruption is safe and fast for
already-completed days (a manifest tracks progress by content hash).

`flush_every_days` (default 20, also settable via `marketdata:
flush_every_days:`) batches store writes rather than writing after every
single day — `MinuteBarStore.write()` reads/merges/rewrites a symbol's
entire year-partition file per call.

**Backfilling completeness sidecars for already-ingested data:**

```bash
uv run python scripts/backfill_completeness.py            # every symbol currently in the bucket
uv run python scripts/backfill_completeness.py --symbol AAPL --symbol MSFT
uv run python scripts/backfill_completeness.py --force     # recompute even where a sidecar already exists
uv run python scripts/backfill_completeness.py --workers 16  # default: 8 -- I/O-bound, safe to raise
```

Safe to re-run: without `--force` it skips any symbol-year that already
has a sidecar.

**Ongoing daily ingestion:** two scheduled GitHub Actions workflows,
gated on the test suite passing first: `.github/workflows/ingest_minute_bars.yml`
and `.github/workflows/ingest_eod_bars.yml`. Both run daily via
`schedule:`, plus `workflow_dispatch` for an on-demand run.

</details>

## Reference data — splits, dividends, IPOs, short interest/volume, float

Corporate actions and positioning data for every US-listed ticker, from
the same vendor (Massive, formerly Polygon.io) as the minute bars above,
ingested into the same R2 bucket so it stays queryable even after
subscription access to the vendor lapses. Same `open_duckdb()`/DuckDB-
macro pattern as `minute_bars`/`eod_bars` — no Python wrapper class.

```bash
pip install "tam-quant[marketdata]"   # same extra as minute bars — adds the `massive` SDK
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

### R2 layout

Grouped under two top-level prefixes by what the data represents, not by
which vendor call fetched it: `corporate_actions/` (things a company
actively does) and `positioning/` (things about how its shares are held
or traded).

| Dataset | R2 path | Partitioning |
|---|---|---|
| Splits | `corporate_actions/splits/<year>.parquet` | year, append-only |
| Dividends | `corporate_actions/dividends/<year>.parquet` | year, append-only |
| IPOs | `corporate_actions/ipos/all.parquet` | single file, full refresh every run |
| Short volume | `positioning/short_volume/<TICKER>/<year>.parquet` | **ticker + year**, append-only |
| Short interest | `positioning/short_interest/<TICKER>/<year>.parquet` | **ticker + year**, append-only |
| Float | `positioning/float/all.parquet` | single file, full refresh every run |

Short volume and short interest are the two datasets partitioned
per-ticker (`positioning/short_volume/AAPL/2025.parquet`, same layout as
minute bars' `minute/<SYMBOL>/<year>.parquet`) — every other dataset here
is small enough market-wide that a single global year (or all-time) file
is fine. Short volume in particular is a daily figure for every US
ticker; a single global year file for it runs into the millions of rows.

Each group also has its own manifest tracking incremental-ingest
cursors: `corporate_actions/_manifest.json`, `positioning/_manifest.json`.

<details>
<summary>Ingesting new reference data (maintainers only — not needed to query the lake)</summary>

```bash
python -m examples.ingest_reference_data --local-root data   # local dry-run, no R2/vendor credentials needed
python -m examples.ingest_reference_data                      # real R2 + MASSIVE_API_KEY
```

Needs `MASSIVE_API_KEY` — the vendor's REST bearer token, a **different**
credential from the `MASSIVE_S3_ACCESS_KEY_ID`/`MASSIVE_S3_SECRET_ACCESS_KEY`
flat-file keys minute bars use. Splits/dividends/short volume/short
interest resume from their own stored cursor automatically; IPOs/float
have no incremental concept and re-fetch their full current table every
run. Scheduled daily via `.github/workflows/ingest_reference_data.yml`
(same gated-on-tests, `schedule:` + `workflow_dispatch` shape as the
other ingestion workflows).

</details>

## Data model notes — things that can silently break a backtest

- **Timestamps are UTC, timezone-aware, always** (`tam.marketdata.schema.TS`)
  — never naive. Convert with `.tz_convert("America/New_York")` only at
  query/display time.
- **Survivorship bias**: solved by filtering ingestion through
  `UniverseProvider.constituents(day)` (point-in-time, not today's list) —
  spot-check your provider's historical add/remove dates before trusting
  it across a long horizon, and avoid re-deriving today's membership list
  downstream by convenience (that reintroduces the exact bias point-in-time
  ingestion was meant to avoid).
- **Ticker changes/reuse**: raw bars are keyed by the ticker as traded that
  day. A rename (e.g. FB → META) or a reused ticker will silently merge or
  split what should be one continuous instrument unless you maintain a
  security-master mapping and join on that instead of the raw ticker.
- **Splits/dividends**: `MassiveFlatFileProvider` defaults `adj_close` to
  `close` when the raw feed carries no separate adjustment — confirm
  whether your actual flat-file product is raw or already split-adjusted
  before computing returns across a split boundary using `open`/`high`/
  `low`/`close` directly.
- **Delistings**: a delisted symbol's bars simply stop — expected, not a
  gap to backfill. Don't forward-fill past a symbol's last real bar.
