# SEC

`tam.research.data.sec` is its own small R2-backed data lake (same bucket
as [market data](marketdata.md), under `sec/`) — raw XBRL facts (full
fidelity: taxonomy, unit, accession number, filed date, ...) and a
derived, normalized `financials` layer (long format: one row per line
item, e.g. `revenue`/`net_income`/`total_assets`).

```bash
pip install "tam-quant[sec]"
```

Not exposed as `tam.Sec` at the top level like `tam.Fred` — import the
class explicitly:

```python
from tam.research.data.sec import Sec

# No construction needed -- Sec.financials()/.filings()/.query() work
# directly on the class, via a shared default instance (reads from R2
# via the usual TAM_PAT token):
Sec.financials(tickers=["AAPL", "MSFT"], statement="income_statement", start=2015)
Sec.filings(ticker="AAPL", forms=["10-K", "10-Q"], start="2015-01-01")
Sec.query("SELECT cik, fiscal_year, value FROM sec_stmt('income_statement') WHERE line_item = 'revenue'")

# Or construct your own instance for a different connection (raw R2
# credentials, a local Parquet tree, ...) -- separate from the shared
# default above:
sec = Sec(local_root="data")
sec.financials(tickers=["AAPL"])
```

Every `Sec` method takes tickers OR raw CIKs interchangeably (`"AAPL"` or
`320193` both work), resolved via the same `sec/reference/
company_tickers.parquet` file EdgarTools' own ticker resolution is backed by.

## Discovering valid inputs

Every input you have to pick a value for has a matching discovery method
that returns the real, legal options as a dataframe:

```python
Sec.companies(search="tesla")                       # find a ticker/CIK: cik, ticker, entity_name
Sec.statements()                                     # valid statement= values
Sec.line_items(tickers=["AAPL"], search="rev")       # valid line_items= values for THIS company, ranked by fact_count
Sec.line_item_catalog(statement="balance_sheet")     # every line item we know how to normalize, whether or not AAPL reports it
Sec.concepts("revenue", tickers=["AAPL"])            # which raw XBRL tags rolled up into "revenue", per company
Sec.forms(tickers=["AAPL"])                          # valid forms= values for filings(), ranked by count
```

`line_items` accepts any canonical line-item name our normalization layer
knows — `revenue`, `net_income`, `cost_of_revenue`, `gross_profit`,
`operating_income`, `ebitda`, `earnings_per_share_basic`/
`earnings_per_share_diluted`, `operating_cash_flow`/`investing_cash_flow`/
`financing_cash_flow`/`free_cash_flow`, `total_assets`/`total_liabilities`/
`stockholders_equity`, and more — `Sec.line_items()`/`Sec.line_item_catalog()`
above are the current, authoritative list.

## `financials()` vs. raw SQL

`Sec.financials()`/`Sec.filings()` (the Python wrappers) do a couple of
things raw SQL doesn't: date columns come back as real dates (cast in the
query itself, not pandas afterward), rows are pre-sorted, and — the one
genuinely non-obvious part — a single filing often reports BOTH a
discrete-quarter figure and a year-to-date cumulative one under the SAME
`end_date` for the same `line_item` (SEC's own `fiscal_year`/
`fiscal_period` labels don't distinguish them). `financials()` defaults to
keeping only the shortest reported duration per `(cik, line_item,
end_date)` — the discrete period — via a window function pushed into the
query; pass `dedupe_periods=False` to get every period SEC reported
instead (e.g. if you specifically want the YTD figures too).

## Querying with raw SQL

Already wired into `open_duckdb()`/`connect()` (see [Market data](marketdata.md)),
so the same macros work directly in raw SQL over either connection:

```python
con.sql("SELECT * FROM sec_stmt('income_statement', 'AAPL') ORDER BY fiscal_year").df()
con.sql("SELECT * FROM sec_stmt('income_statement') WHERE line_item = 'revenue'").df()   # every company at once
con.sql("SELECT * FROM sec_facts('AAPL')").df()        # raw XBRL, full fidelity
con.sql("SELECT * FROM sec_filings('AAPL')").df()      # filing metadata: accession number, form, filed date, ...
con.sql("SELECT * FROM sec_companies() WHERE ticker = 'AAPL'").df()   # ticker/CIK/name reference table
```

## Plotting a fundamentals trend

```python
import pandas as pd
from tam.research.data.sec import Sec
from tam.charting import timeseries

financials = Sec.financials(tickers=["AAPL"], line_items=["revenue", "net_income", "operating_cash_flow"])

def series_for(line_item: str, label: str, n: int = 32) -> pd.Series:
    rows = financials[financials["line_item"] == line_item].sort_values("end_date")
    return rows.set_index("end_date")["value"].tail(n).rename(label)

timeseries([series_for("revenue", "Revenue"), series_for("net_income", "Net Income"),
            series_for("operating_cash_flow", "Operating Cash Flow")], title="AAPL fundamentals")
```
