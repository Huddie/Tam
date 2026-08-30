# Data storage layout

Every dataset `tam` persists lives in **one Cloudflare R2 bucket**, split into
non-overlapping top-level prefixes — one per dataset below. Locally (tests,
offline dev), the same layout exists under a plain directory instead of a
bucket: `<local_root>/<prefix>/...`. Every "Example usage" pair below shows
the same query two ways — a raw `read_parquet()`/DuckDB-macro call against
the Parquet files directly, and the equivalent call through `tam`'s own
Python API — so you can see exactly what the wrapper is doing underneath.

See [Architecture & background](architecture.md) for *why* each layout is
shaped the way it is (partitioning tradeoffs, ingestion/backfill mechanics).
This page only documents *what's actually on disk*.

## Equity market data

### EOD (end-of-day bars)

**Storage path scheme**

```
<root_or_bucket>/<TICKER>/<year>.<ext>          # tam.data (local: CsvStore/ParquetStore)
<bucket>/eod/<TICKER>/<year>.parquet            # tam.data (R2DataStore, prefix="eod")
<bucket>/eod/<TICKER>/<year>.completeness.json  # sidecar: trading-session coverage
```

One file per ticker per calendar year. `tam.marketdata`'s `eod_bars(sym)`
DuckDB macro reads this exact lake directly (it is not derived from minute
bars) — this is the only source with a real, vendor-provided `adj_close`.

**Table schema** (`tam.data.schema.OHLCV_COLUMNS`, indexed by `date`)

| Column | Type | Notes |
|---|---|---|
| `date` | date (index) | trading date |
| `open` | float64 | |
| `high` | float64 | |
| `low` | float64 | |
| `close` | float64 | |
| `adj_close` | float64 | dividend/split-adjusted; may equal `close` if the provider doesn't supply a separate adjustment |
| `volume` | float64 | |

**Example usage**

```sql
-- Raw SQL, via open_duckdb()
SELECT * FROM eod_bars('AAPL') ORDER BY date;
```

```python
# Tam-native
from tam.data.repository import DataRepository
from tam.data.providers import DataProvider
from tam.data.storage import DataStore
from tam.registry import Registry
from datetime import date

repo = DataRepository(Registry.get(DataProvider, "yfinance"), Registry.create(DataStore, "parquet", "data/eod"))
repo.ingest(["AAPL"], date(2020, 1, 1), date(2024, 1, 1))
df = repo.query("AAPL", date(2023, 1, 1), date(2023, 6, 1))
```

### Minute bars

**Storage path scheme**

```
<bucket>/minute/<SYMBOL>/<year>.parquet
<bucket>/minute/<SYMBOL>/<year>.completeness.json  # sidecar
<bucket>/minute/_manifest.json                     # ingestion resume manifest
```

**Table schema** (`tam.marketdata.schema.MINUTE_BAR_COLUMNS`, indexed by `ts`)

| Column | Type | Notes |
|---|---|---|
| `ts` | timestamp (index) | UTC, timezone-aware, always |
| `symbol` | string | |
| `open` | float64 | |
| `high` | float64 | |
| `low` | float64 | |
| `close` | float64 | |
| `volume` | float64 | |
| `adj_close` | float64 | defaults to `close` if the flat-file feed carries no separate adjustment |
| `transactions` | float64 | trade count in the bar, if the vendor supplies it |

**Example usage**

```sql
SELECT * FROM minute_bars('SPY') WHERE ts >= '2020-03-01' ORDER BY ts;
SELECT * FROM daily_bars('SPY') ORDER BY day;  -- rolled up on the fly, not a separate table
```

```python
from tam.marketdata.store import R2MinuteBarStore  # or LocalMinuteBarStore

store = R2MinuteBarStore()
df = store.read("SPY")
```

### Corporate actions

Splits, dividends, and IPOs — see [Reference data](marketdata.md#reference-data)
for the API surface (`splits()`/`dividends()`/`ipos()` macros,
`MassiveReferenceProvider`).

#### Splits

```
<bucket>/corporate_actions/splits/<year>.parquet
```
Append-only, year-partitioned (global — not per-ticker; a few thousand rows/year across the whole market).

| Column | Type |
|---|---|
| `id` | string |
| `ticker` | string |
| `execution_date` | string (date) |
| `split_from` | float64 |
| `split_to` | float64 |
| `adjustment_type` | string — `forward_split` \| `reverse_split` \| `stock_dividend` |
| `historical_adjustment_factor` | float64 |

```sql
SELECT * FROM splits('AAPL') ORDER BY execution_date;
```
```python
from tam.marketdata.reference_store import R2ReferenceStore

df = R2ReferenceStore().read("splits")
```

#### Dividends

```
<bucket>/corporate_actions/dividends/<year>.parquet
```
Append-only, year-partitioned (global).

| Column | Type |
|---|---|
| `id` | string |
| `ticker` | string |
| `cash_amount` | float64 |
| `currency` | string |
| `declaration_date` | string (date) |
| `distribution_type` | string — `recurring` \| `special` \| `supplemental` \| `irregular` \| `unknown` |
| `ex_dividend_date` | string (date) |
| `frequency` | float64 |
| `historical_adjustment_factor` | float64 |
| `pay_date` | string (date) |
| `record_date` | string (date) |
| `split_adjusted_cash_amount` | float64 |

```sql
SELECT * FROM dividends('AAPL') ORDER BY ex_dividend_date;
```
```python
df = R2ReferenceStore().read("dividends")
```

#### IPOs

```
<bucket>/corporate_actions/ipos/all.parquet
```
Single file, wholesale overwritten every ingestion run (mutable records — status transitions `rumor→pending→new→history`, no cursor concept).

| Column | Type |
|---|---|
| `ticker` | string |
| `issuer_name` | string |
| `ipo_status` | string |
| `announced_date`, `listing_date`, `last_updated` | string (date) |
| `currency_code` | string |
| `final_issue_price`, `lowest_offer_price`, `highest_offer_price` | float64 |
| `min_shares_offered`, `max_shares_offered`, `shares_outstanding`, `total_offer_size`, `lot_size` | float64 |
| `isin`, `us_code`, `primary_exchange`, `security_type`, `security_description` | string |

```sql
SELECT * FROM ipos() ORDER BY listing_date DESC;
```
```python
df = R2ReferenceStore().read("ipos")
```

### Positioning

Short volume, short interest, and float — see
[Reference data](marketdata.md#reference-data) for the API surface.

#### Short volume

```
<bucket>/positioning/short_volume/<TICKER>/<year>.parquet
```
**Per-ticker** + year-partitioned — unlike the corporate-actions datasets
above. This one is a daily FINRA figure for every US-listed ticker; a
single global year file was confirmed to reach 3.1M rows, so it's split
the same way minute bars are (`minute/<SYMBOL>/<year>.parquet`).

| Column | Type |
|---|---|
| `ticker` | string |
| `date` | string (date) |
| `short_volume`, `total_volume`, `short_volume_ratio` | float64 |
| `exempt_volume`, `non_exempt_volume` | float64 |
| `adf_short_volume`, `adf_short_volume_exempt` | float64 |
| `nasdaq_carteret_short_volume(_exempt)` | float64 |
| `nasdaq_chicago_short_volume(_exempt)` | float64 |
| `nyse_short_volume(_exempt)` | float64 |

```sql
SELECT * FROM short_volume('AAPL') ORDER BY date;
```
```python
df = R2ReferenceStore().read("short_volume", ticker="AAPL")
```

#### Short interest

```
<bucket>/positioning/short_interest/<TICKER>/<year>.parquet
```
Per-ticker + year-partitioned, biweekly cadence.

| Column | Type |
|---|---|
| `ticker` | string |
| `settlement_date` | string (date) |
| `short_interest`, `avg_daily_volume`, `days_to_cover` | float64 |

```sql
SELECT * FROM short_interest('AAPL') ORDER BY settlement_date;
```
```python
df = R2ReferenceStore().read("short_interest", ticker="AAPL")
```

#### Float

```
<bucket>/positioning/float/all.parquet
```
Single file, wholesale overwritten every run (the vendor endpoint only
serves "the latest" free float — no date-range param exists).

| Column | Type |
|---|---|
| `ticker` | string |
| `effective_date` | string (date) |
| `free_float`, `free_float_percent` | float64 |

```sql
SELECT * FROM float_data('AAPL');
```
```python
df = R2ReferenceStore().read("float")
```

Manifests (ingestion resume cursors, one per group): `corporate_actions/_manifest.json`, `positioning/_manifest.json`.

## Company fundamentals (SEC)

All four layers below share one prefix (`sec/`) in the same bucket, one
non-overlapping prefix from everything above. See
[SEC reference](research-sec.md) for the `Sec` class / `sec_*` macro API.

### Company reference

```
<bucket>/sec/reference/company_tickers.parquet
```
Single file — ticker/CIK/name lookup table, the whole market.

| Column | Type |
|---|---|
| `cik` | int |
| `ticker` | string |
| `entity_name` | string |

```sql
SELECT * FROM sec_companies();
```
```python
from tam.research.data.sec import Sec

df = Sec().companies()
```

### Filings / submissions

```
<bucket>/sec/submissions/<fiscal_year>.parquet
```
Fiscal-year-partitioned, upserted per CIK within a partition (not one file
per company — avoids millions of tiny files).

| Column | Type |
|---|---|
| `cik` | int |
| `accession_number` | string |
| `form` | string |
| `filed_date` | string (date) |
| `period_of_report` | string (date) |
| `primary_document` | string |
| `is_xbrl` | bool |

```sql
SELECT * FROM sec_filings('AAPL') ORDER BY filed_date DESC;
```
```python
df = Sec().filings("AAPL")
```

### XBRL facts (raw)

```
<bucket>/sec/facts/<taxonomy>/<fiscal_year>.parquet
```
Partitioned by taxonomy (e.g. `us-gaap`) + fiscal year, upserted per CIK.
Every fact SEC's own XBRL API reports — pre-normalization.

| Column | Type |
|---|---|
| `cik` | int |
| `entity_name` | string |
| `taxonomy`, `concept`, `unit` | string |
| `fact_type` | string — `instant` \| `duration` |
| `start_date`, `end_date` | string (date) |
| `fiscal_year` | int |
| `fiscal_period` | string — `Q1` \| `Q2` \| `Q3` \| `FY` |
| `form` | string |
| `filed_date` | string (date) |
| `accession_number` | string |
| `frame` | string |
| `dimensions` | string (JSON-encoded `{axis: member}`, or null for the whole-company total) |
| `context_id` | string |
| `value` | float64 |

```sql
SELECT * FROM sec_facts('AAPL') WHERE concept = 'Revenues';
```
```python
df = Sec().query("AAPL", concept="Revenues")
```

### Financials (normalized)

```
<bucket>/sec/financials/<fiscal_year>.parquet
```
Fiscal-year-partitioned, upserted per CIK — raw facts normalized into a
consistent `statement`/`line_item` shape across companies (see
`Sec.statements()`/`Sec.line_items()` for the live, current value sets).

| Column | Type |
|---|---|
| `cik` | int |
| `fiscal_year` | int |
| `fiscal_period` | string |
| `start_date`, `end_date` | string (date) |
| `accession_number` | string |
| `filed_date` | string (date) |
| `statement` | string — `balance_sheet` \| `cash_flow` \| `income_statement` \| `metrics` |
| `line_item` | string — normalized name, e.g. `revenue` \| `net_income` \| `total_assets` |
| `concept` | string — the raw XBRL concept this line item was mapped from |
| `value` | float64 |

```sql
SELECT * FROM sec_stmt('income_statement', 'AAPL') ORDER BY fiscal_year;
```
```python
df = Sec().financials(tickers=["AAPL"], statement="income_statement")
```

Manifest: `sec/_manifest.json`.

## Macro data (FRED)

**No persistent storage.** `tam.Fred` is a thin passthrough over
`fredapi.Fred.get_series()` with only an in-memory, per-process cache
(1-day TTL) — nothing is ever written to disk or R2. There's no path
scheme or table schema to document here; see [FRED reference](research-fred.md)
for the API itself.
