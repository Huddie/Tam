# FRED

Macro/economic series (Treasury yields, Fed Funds rate, CPI, unemployment,
...) via [FRED](https://fred.stlouisfed.org). A thin wrapper around
`fredapi.Fred`, exposed at the top level as `tam.Fred`:

```bash
pip install "tam-quant[fred]"
```

```python
import tam

dgs10 = tam.Fred.get(tam.Fred.Datasets.TREASURY_10Y)   # or tam.Fred.get("DGS10") -- same series
dgs10.name    # "10-Year Treasury Yield", not the raw "DGS10" code
dgs10.tail()
```

`tam.Fred.Datasets` covers a handful of commonly-used series as a memory
aid (`TREASURY_3MO`/`TREASURY_2Y`/`TREASURY_10Y`/`TREASURY_30Y`,
`FED_FUNDS_RATE`, `FED_FUNDS_EFFECTIVE`, `SOFR`, `CPI`,
`UNEMPLOYMENT_RATE`, `YIELD_CURVE_10Y_2Y`) — FRED has tens of thousands of
series total, so pass any other raw series id (a plain string) straight to
`.get()` just the same:

```python
tam.Fred.get("DGS2", start="2015-01-01", end="2024-01-01")   # start/end optional; omit either for full history
```

## Authentication

```python
fred_key = tam.Secrets["FRED_API_KEY"]      # raises a clear error if not set anywhere
fred_key = tam.Secrets.get("FRED_API_KEY")  # None instead of raising
```

Resolution order: an environment variable (directly, or via a local
`.env` file), then — if running in Colab — a Colab secret of that same
name. The underlying `fredapi.Fred` client is built lazily on first
`.get()` call; `import tam` or referencing `tam.Fred.Datasets` never
requires a key to be configured.

## Plotting a series

`tam.Fred.get(...)` already returns a plain named `pd.Series`, so it plots
with no special-casing via [`timeseries()`](charting.md):

```python
from tam.charting import timeseries

timeseries([tam.Fred.get(tam.Fred.Datasets.TREASURY_2Y), tam.Fred.get(tam.Fred.Datasets.TREASURY_10Y)], title="Treasury Yields")
```
