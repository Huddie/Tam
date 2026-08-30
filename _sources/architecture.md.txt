# Architecture & background

*Why* things are built the way they are, how each data lake actually gets
populated (backfills, daily ingestion, credentials), and gotchas that can
silently break a backtest. If you just want to know what to *call*, see
[Market data](marketdata.md) / [SEC](research-sec.md) / [Data](data.md)
instead; if you want to know what's actually *on disk*, see
[Data storage layout](storage-layout.md).

## Market data (minute bars + reference data)

### Ingestion pipeline

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
- **`tam.marketdata.store.MinuteBarStore`** — year-partitioned Parquet
  (see [storage layout](storage-layout.md#minute-bars)) over any
  `pyarrow.fs.FileSystem` for local disk, or boto3 directly for R2 (its own
  class — deliberately NOT `pyarrow.fs.S3FileSystem`, which was observed
  failing R2's multipart-upload handshake in production). Every write also
  (re)computes a completeness sidecar (actual-vs-expected trading-session
  minutes per day/month/year, from the NYSE calendar) —
  [tam-data-explorer](tam-data-explorer.md) reads this back verbatim to
  drive its completeness badge.
- **`tam.marketdata.ingest`** — wires the above into a resumable,
  idempotent backfill (a JSON manifest tracks which days are already done).
- **`tam.marketdata.duckdb_query.open_duckdb()`** — the query entry point;
  works identically from a local machine, Colab, or CI.

Reference data (splits/dividends/IPOs/short volume/short interest/float)
follows the same shape, from the same vendor, into the same bucket, but
through `tam.marketdata.reference_provider`/`reference_store`/
`reference_ingest` instead — kept as separate modules since the fetch
shape differs (global paginated feeds, not one flat file per day) and two
of the six datasets need per-ticker partitioning the bar lake doesn't
(see [storage layout](storage-layout.md#positioning) for why).

### R2 setup

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
   `MASSIVE_S3_ACCESS_KEY_ID` / `MASSIVE_S3_SECRET_ACCESS_KEY`. Reference
   data needs a separate credential, `MASSIVE_API_KEY` (a REST bearer
   token, a different Massive product surface entirely).

No boto3 dependency for the bar lake specifically: reads/writes go through
`pyarrow.fs.S3FileSystem`; interactive querying goes through DuckDB's own
`httpfs` extension. (Reference data's own store *does* use boto3 directly,
same reasoning as the multipart-upload note above.)

### Backfilling / ingesting new data

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

Reference data has its own CLI, no universe/concurrency to configure
(every dataset is a global feed, not per-symbol):

```bash
python -m examples.ingest_reference_data --local-root data   # local dry-run, no R2/vendor credentials needed
python -m examples.ingest_reference_data                      # real R2 + MASSIVE_API_KEY
```

Splits/dividends/short volume/short interest resume from their own stored
cursor automatically; IPOs/float have no incremental concept and re-fetch
their full current table every run (mutable records / no date-range param
on that vendor endpoint at all).

**Backfilling completeness sidecars for already-ingested minute bars:**

```bash
uv run python scripts/backfill_completeness.py            # every symbol currently in the bucket
uv run python scripts/backfill_completeness.py --symbol AAPL --symbol MSFT
uv run python scripts/backfill_completeness.py --force     # recompute even where a sidecar already exists
uv run python scripts/backfill_completeness.py --workers 16  # default: 8 -- I/O-bound, safe to raise
```

Safe to re-run: without `--force` it skips any symbol-year that already
has a sidecar.

**Ongoing daily ingestion:** three scheduled GitHub Actions workflows,
each gated on the test suite passing first: `.github/workflows/ingest_minute_bars.yml`,
`.github/workflows/ingest_eod_bars.yml`, and
`.github/workflows/ingest_reference_data.yml`. All three run daily via
`schedule:`, plus `workflow_dispatch` for an on-demand run.

### Data model notes — things that can silently break a backtest

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

## SEC / company fundamentals

Raw XBRL facts and normalized financials are two separate layers in the
same lake (see [storage layout](storage-layout.md#company-fundamentals-sec))
because the normalization step is lossy by design — collapsing many raw
vendor-specific/company-specific XBRL tags into one canonical `line_item`
name loses the ability to tell which raw concept a number came from,
which `Sec.concepts()` still needs. Both layers are fiscal-year-partitioned
(not per-company) specifically to avoid ending up with one Parquet file
per CIK per year — every write is a CIK-scoped upsert within a shared
partition instead.

Backfilling this lake (maintainer-only, needs R2 write + EdgarTools
network access): `scripts/backfill_sec_facts.py` populates the raw facts
layer from SEC's own XBRL API; `scripts/rebuild_sec_financials.py`
re-derives the normalized financials layer from whatever raw facts are
already stored, so re-running it after a normalization-logic change
doesn't require re-fetching anything from SEC.

## EOD cache (`tam.data`)

`DataStore` is a plain `Registry` entry (`"csv"`/`"parquet"` built in) —
the same year-partitioned layout works identically for a bare local
directory or `R2DataStore` (prefix `"eod"`, same bucket and credential
resolution as market data above); swapping backends is a one-line
`Registry.create(...)` change, not a code change. `DataRepository` caches
in-memory after the first read per process, on top of whichever `DataStore`
backs it — repeated `.query()` calls for an already-fetched range never
re-hit the store at all, let alone the network.
