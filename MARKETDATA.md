# Historical 1-minute market-data lake (`tam.marketdata`)

A ~20-year historical dataset of 1-minute OHLCV bars for SPY and the
point-in-time S&P 500 universe (no survivorship bias -- see below), stored as
Parquet in Cloudflare R2 and queried directly with DuckDB. Daily/weekly/
monthly/N-minute rollups, returns, and volatility are **derived on demand**
from the 1-minute data at query time, not precomputed -- see "Querying" below.

This is a separate concern from `tam.data`'s own end-of-day cache (used by
backtests), sharing conventions (the same `Registry` pattern, the same
symbol/year Parquet partitioning) but not its code -- see each module's
docstring for why.

## Architecture at a glance

```
Massive flat files  -->  filter to universe  -->  validate  -->  R2 (Parquet)
  (MinuteBarProvider)   (tam.basket.universe)  (tam.marketdata.validate)  (MinuteBarStore)
```

- **`tam.marketdata.providers.MinuteBarProvider`** -- fetches one vendor's
  full-market flat file for one day. `MassiveFlatFileProvider` ships built
  in, verified against Massive's own "Flat Files Quickstart" docs and
  confirmed live (downloaded a real day; CSV header matched exactly). Any
  other "one flat file per day" vendor needs no new code either, just
  `Registry.create(MinuteBarProvider, "flatfile_s3", endpoint=..., bucket=...,
  key_template=..., column_map={...}, ...)` -- see that module's docstring.
- **`tam.basket.universe.UniverseProvider`** -- reused as-is (not
  duplicated) for point-in-time S&P 500 membership. `pitindex` (Python
  >=3.11) is the recommended source; verify its historical coverage against
  a source you trust before relying on it for the full 20 years -- see
  "Survivorship bias" below.
- **`tam.marketdata.validate`** -- OHLC integrity + trading-session-coverage
  checks, run before anything is written.
- **`tam.marketdata.store.MinuteBarStore`** -- year-partitioned Parquet,
  `<root>/<SYMBOL>/<year>.parquet`, over any `pyarrow.fs.FileSystem` --
  `local_parquet` (plain disk, what tests/dev use) and `r2_parquet`
  (Cloudflare R2) are the same class, just pointed at a different
  filesystem. Every write also (re)computes and persists a completeness
  sidecar -- see `tam.marketdata.completeness` below.
- **`tam.marketdata.completeness`** -- actual-vs-expected trading-session
  minutes per day/month/year for one symbol-year, computed from the NYSE
  calendar (`pandas_market_calendars`, the same optional dependency
  `tam.marketdata.validate`'s own session-coverage check uses). Written as
  `<root>/<SYMBOL>/<year>.completeness.json` next to that year's own
  `.parquet` file every time `MinuteBarStore.write()` touches it -- tam-
  data-explorer's Worker reads this back verbatim (never recomputes it) to
  drive the year/month/day/range completeness badge on the file-viewer
  page. Data ingested before this existed (or without the `marketdata`
  extra installed at ingest time) has no sidecar until backfilled --
  see `scripts/backfill_completeness.py`.
- **`tam.marketdata.ingest`** -- wires the above into a resumable,
  idempotent backfill (a JSON manifest tracks which days are already done).
- **`tam.marketdata.duckdb_query.open_duckdb()`** -- the query entry point;
  works identically from a local machine, Colab, or CI.

## R2 setup

1. Create an R2 bucket in the Cloudflare dashboard (e.g. `tam-data`).
2. Create **two** API tokens (R2 -> Manage API Tokens): a **read-only** one
   for querying, and a **read-write** one for ingestion. Never use the
   write token from a notebook.
3. Set credentials -- see `.env.example` for the exact names
   (`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
   `R2_BUCKET`). Resolution order (see
   `tam.marketdata.credentials.resolve_r2_credentials`): explicit kwarg ->
   env var -> Colab secret (same name, via the key-icon panel) -> a saved
   file (`save_r2_credentials(...)`, written to
   `~/.config/tam-marketdata/r2_credentials.json`) -- the same layered
   resolution `tam.discovery`'s own publishing-token auth already
   established, just for several S3-style fields instead of one bearer
   token.
4. For Massive flat-file access, generate **S3-style** keys from your
   Massive dashboard (NOT a regular REST `apiKey`) and set
   `MASSIVE_S3_ACCESS_KEY_ID` / `MASSIVE_S3_SECRET_ACCESS_KEY`.

No boto3 dependency: reads/writes go through `pyarrow.fs.S3FileSystem`
(pyarrow is already a hard dependency of this project); interactive querying
goes through DuckDB's own `httpfs` extension. Costs at this data's actual
scale (SPY + ~600-800 point-in-time tickers x 20 years of 1-minute bars) are
small: on the order of tens of GB, a few dollars a month in R2 storage, with
**zero R2 egress fees** -- the main recurring cost is your Massive
subscription itself, not infrastructure.

### Backfilling completeness sidecars for already-ingested data

Every new write produces a completeness sidecar automatically (see
`tam.marketdata.completeness` above), but symbol-years ingested before that
existed don't have one yet. Run once, from a machine with real R2
read-write credentials configured:

```bash
uv run python scripts/backfill_completeness.py            # every symbol currently in the bucket
uv run python scripts/backfill_completeness.py --symbol AAPL --symbol MSFT
uv run python scripts/backfill_completeness.py --force    # recompute even where a sidecar already exists
uv run python scripts/backfill_completeness.py --workers 16  # default: 8 -- I/O-bound, safe to raise
```

Safe to re-run: without `--force` it skips any symbol-year that already
has a sidecar, so an interrupted run only redoes the remaining work next
time. Runs symbol-years concurrently (a thread pool -- see the script's own
docstring); one failure is reported at the end without aborting the rest.


## Backfilling data

Validate against a small range/universe locally first (no R2/Massive
credentials needed -- see `examples/ingest_minute_bars_config.yaml`):

```bash
python -m examples.ingest_minute_bars examples/ingest_minute_bars_config.yaml
```

Then point the same CLI at real credentials for a production backfill --
swap `store: local_parquet` for `store: r2_parquet` and `universe: static`
for `universe: pitindex` in your own config (see that example file's
comments for the exact fields; `provider: massive_flatfiles` already needs
no further overrides). A large multi-year backfill is a one-off job -- run
it locally or on a dedicated machine, not inside a GitHub Actions job (which
has a runtime limit); re-running it after an interruption is safe and fast
for already-completed days (see "Resumability" below).

**Tuning a large backfill**: `ingest()`/`run_ingest()` batch store writes via
`flush_every_days` (default 20, also settable from a config's `marketdata:
flush_every_days:`) rather than writing after every single day --
`MinuteBarStore.write()` reads/merges/rewrites a symbol's ENTIRE
year-partition file per call, so writing once per day would make a 10-year
backfill re-read-and-rewrite an ever-growing file up to ~252 times per
symbol per year. A larger `flush_every_days` means fewer, bigger store
writes (faster overall) at the cost of losing more already-fetched work if
the process is interrupted mid-batch (safe to resume either way -- just
re-fetches whatever wasn't flushed yet, never corrupts already-written
data).

### Ongoing daily ingestion (GitHub Actions)

Two separate scheduled workflows, one per data source, each gated on the test
suite passing first:

- `.github/workflows/ingest_minute_bars.yml` catches up the prior trading
  day's 1-minute bars, using `examples/ingest_minute_bars_daily_config.yaml`.
- `.github/workflows/ingest_eod_bars.yml` catches up daily/EOD bars (the
  full historical S&P 500 universe + a curated list of common indices/ETFs)
  via `scripts/backfill_sp500_eod.py` / `scripts/backfill_indices_eod.py`.

Both run daily via `schedule:` (staggered 10:00/10:30 UTC so their R2 writes
don't overlap), plus `workflow_dispatch` for an on-demand run from the
Actions tab or `gh workflow run "<name>"`. This means a CI runner holds R2
(and, for minute bars, Massive) write credentials on a recurring, unattended
schedule -- previously `ingest_minute_bars.yml` was deliberately manual-only
to avoid exactly that; it's since been switched to a daily schedule.
Removing a workflow's `schedule:` trigger reverts it to manual-only with no
other change needed, if that tradeoff should be revisited.

**Repository secrets** required (Settings -> Secrets and variables ->
Actions): `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
`R2_BUCKET` (both workflows), plus `MASSIVE_S3_ACCESS_KEY_ID`,
`MASSIVE_S3_SECRET_ACCESS_KEY` (minute bars only -- yfinance needs no API
key at all). Without them, a run fails fast with a clear "missing
credential" error rather than silently doing nothing. Prefer running the
same ingestion locally instead if you'd rather not use GitHub Actions for
this at all -- see "Backfilling data" above.

### Resumability

Re-running `ingest()`/the CLI over an already-completed date range is a fast
no-op per day -- a small manifest (`_manifest.json`, stored alongside the
data itself) tracks which days are already ingested by content hash. A day
the vendor later republishes with corrected data is detected (hash changed)
and re-ingested automatically, not skipped.

## Querying

```python
from tam.marketdata.duckdb_query import open_duckdb

con = open_duckdb(bucket="tam-data")   # reads from R2 directly
# con = open_duckdb(local_root="data/minute")  # or: plain local Parquet, no R2/network

con.sql("SELECT * FROM minute_bars('SPY') WHERE ts >= '2020-03-01' ORDER BY ts").df()
con.sql("SELECT * FROM daily_bars('SPY') ORDER BY day").df()
con.sql("SELECT * FROM weekly_bars('SPY') ORDER BY week").df()
con.sql("SELECT * FROM monthly_bars('SPY') ORDER BY month").df()
con.sql("SELECT * FROM rollup_bars('SPY', 5) ORDER BY bucket").df()   # any N-minute bars
con.sql("SELECT * FROM daily_returns('SPY') ORDER BY day").df()
con.sql("SELECT * FROM rolling_volatility('SPY', 21) ORDER BY day").df()  # 21-day annualized vol
```

Every one of these is a DuckDB SQL macro over the raw 1-minute Parquet files
-- nothing is precomputed or stored ahead of time; add your own macro (or
just write ad hoc SQL against `minute_bars(...)`/`read_parquet(...)`
directly) for any other rollup or research feature you need.

### From Google Colab

Colab has no `.env` file. Set the same credentials as **Colab secrets**
(key-icon panel in the left sidebar, same names as the env vars above, e.g.
`R2_ACCESS_KEY_ID`) and grant the notebook access -- `resolve_r2_credentials()`
picks them up automatically, no code changes needed versus running locally.
This replaces the Drive-mount workaround `NOTEBOOK.md` documents for
backtest data/reports: R2 is now the shared, persistent store, reachable
identically from Colab, your laptop, or CI.

Install the extra first:

```python
!pip install -q "tam-quant[marketdata]"
```

## Data model notes / things that can silently break a backtest

- **Timestamps are UTC, timezone-aware, always** (`tam.marketdata.schema.TS`)
  -- never naive, and never stored pre-converted to `America/New_York`.
  Market-hours boundaries cross a DST transition twice a year, at which
  point a naive local timestamp is genuinely ambiguous. Convert with
  `.tz_convert("America/New_York")` only at query/display time.
- **Survivorship bias**: solved by filtering ingestion through
  `UniverseProvider.constituents(day)` (point-in-time, not today's list) --
  but this is only as good as that provider's own historical accuracy.
  Spot-check `pitindex`'s (or whatever source you use) historical add/remove
  dates against a source you trust before relying on it across the full 20
  years, and watch for the same footgun again downstream: joining minute
  bars to "was this a constituent on this date" using **today's** membership
  list (out of convenience in a notebook) reintroduces the exact bias
  point-in-time ingestion was meant to avoid.
- **Ticker changes/reuse**: raw bars are keyed by the ticker as traded that
  day. A rename (e.g. FB -> META) or a reused ticker will silently merge or
  split what should be one continuous instrument unless you maintain a
  security-master mapping (stable id -> ticker validity windows) and join on
  that instead of the raw ticker for anything spanning a rename.
  `tam.marketdata` doesn't ship this yet -- add it before trusting a
  long-horizon return calculation across a rename boundary.
- **Splits/dividends**: `MassiveFlatFileProvider` defaults `adj_close` to
  `close` when the raw feed carries no separate adjustment (same convention
  `tam.data.providers`' existing providers already use) -- confirm whether
  your actual flat-file product is raw or already split-adjusted before
  computing returns across a split boundary using `open`/`high`/`low`/`close`
  directly.
- **Delistings**: a delisted symbol's bars simply stop -- that's expected,
  not a gap to backfill. Don't forward-fill past a symbol's last real bar.
- **If a real backfill starts failing where it didn't before**: Massive's
  flat-file schema could shift over time even though it's currently verified
  (see above) -- `MassiveFlatFileProvider`'s object-key template and CSV
  column names are all constructor overrides for exactly this reason; check
  Massive's current Flat Files docs and pass the corrected values rather than
  editing the class.

## Testing

`tests/test_marketdata_*.py` -- no real network/R2/Massive calls anywhere
(matching this project's existing test convention): stores are tested
against `pyarrow.fs.LocalFileSystem`, providers against a locally-written
fake flat file, and DuckDB/`pandas_market_calendars`-dependent tests
`pytest.importorskip` cleanly when the `marketdata` extra isn't installed.
