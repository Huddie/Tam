# tam

Config-driven event backtesting for stocks/indices, plus two small sites
built on top of the same market-data lake.

`tam` is published on PyPI as `tam-quant`:

```bash
pip install tam-quant
```

```{toctree}
:caption: Get started
:maxdepth: 1

getting-started
notebooks
```

```{toctree}
:caption: API reference
:maxdepth: 1

data
strategy
portfolio
backtest
reporting
charting
basket
marketdata
research-fred
research-sec
```

```{toctree}
:caption: Data storage layout
:maxdepth: 1

storage-layout
```

```{toctree}
:caption: Architecture & background
:maxdepth: 1

architecture
```

```{toctree}
:caption: Sites
:maxdepth: 1

tam-discovery
tam-data-explorer
```

## The three components

| Component | What it is |
|---|---|
| `tam` | Python library — data ingestion, strategies, backtesting, reporting |
| [tam-discovery](tam-discovery.md) | Private catalog site for publishing backtest report HTML |
| [tam-data-explorer](tam-data-explorer.md) | Browser + SQL client for the OHLCV minute-bar lake |

`tam-discovery` and `tam-data-explorer` are Cloudflare Workers + React UIs
that `tam` talks to over HTTP — neither is required to use `tam` itself.
