# tam-data-explorer

Browse and export the OHLC minute-bar Parquet lake in R2 (the same bucket
`tam.marketdata` writes to) from a browser or a script — a paginated table
view, a folder/symbol-year browser, export as Parquet or combined CSV, and
full SQL access from Python via a self-service personal token.

Cloudflare Worker + React UI, in `tam-data-explorer/`. Companion Python
client: `tam/marketdata/explorer_client.py`. Full deployment runbook:
[tam-data-explorer/README.md](https://github.com/Huddie/Tam/blob/main/tam-data-explorer/README.md).

## Using it from Python

Create a token in the app's "Personal tokens" page, then:

```bash
export TAM_PAT=<personal token>
```

```python
from tam.marketdata.explorer_client import fetch_dataframe, connect

fetch_dataframe("AAPL", 2024).head()

connect().sql("SELECT * FROM daily_bars('AAPL') LIMIT 5").df()
```

`connect()` mints short-lived, read-only R2 credentials scoped to your
token — it never hands out real account credentials.

## Local development

```bash
cd tam-data-explorer
npm install
npm run dev         # wrangler dev -- local Worker + SPA
npm run build
```
