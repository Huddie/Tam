# tam-discovery

A private, GitHub-authenticated catalog for publishing and browsing static
HTML research artifacts — backtest dashboards by default, or any
self-contained `.html` file. Publishing is immutable: every upload creates a
new version with its own permanent URL; an optional stable `name` always
resolves to the latest version.

Cloudflare Worker (TypeScript, D1, R2, Access) + React UI, in `tam-discovery/`.
Full deployment runbook: [tam-discovery/README.md](https://github.com/Huddie/Tam/blob/main/tam-discovery/README.md).

## Publishing from Python

```python
from tam.discovery import upload

upload("examples/output/report.html", title="MA crossover", tag="demo")
```

## Publishing from the CLI

```bash
export TAM_DISCOVERY_API_URL=https://discovery.example.com
uv run upload-discovery login
uv run upload-discovery examples/output/report.html --title "Smoke test" --tag demo
```

## Local development

```bash
cd tam-discovery
npm install
npm run dev     # wrangler dev -- local Worker + SPA
npm test        # vitest -- Miniflare-backed D1/R2, no live account needed
```
