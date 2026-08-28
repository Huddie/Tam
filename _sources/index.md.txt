# tam

Config-driven event backtesting for stocks/indices, plus two small sites
built on top of the same market-data lake.

```{toctree}
:maxdepth: 1

tam
tam-discovery
tam-data-explorer
```

## The three pieces

| Component | What it is |
|---|---|
| [tam](tam.md) | Python library — data ingestion, strategies, backtesting, reporting |
| [tam-discovery](tam-discovery.md) | Private catalog site for publishing backtest report HTML |
| [tam-data-explorer](tam-data-explorer.md) | Browser + SQL client for the OHLCV minute-bar lake |

`tam` is published on PyPI as `tam-quant`:

```bash
pip install tam-quant
```

`tam-discovery` and `tam-data-explorer` are Cloudflare Workers + React UIs
that `tam` talks to over HTTP — neither is required to use `tam` itself.
