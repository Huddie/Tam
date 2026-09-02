# Discovery

*Full generated reference: [`tam.discovery`](api/tam.discovery.rst).*

A private, GitHub-authenticated catalog for publishing and browsing static
HTML research artifacts — backtest dashboards by default, or any
self-contained `.html` file. Publishing is immutable: every upload creates
a new version with its own permanent URL; an optional stable `name` always
resolves to the latest version.

Cloudflare Worker (TypeScript, D1, R2, Access) + React UI, in
`tam-discovery/`. This page covers the Python SDK / CLI `tam.discovery`
talks to it with; for the Worker's own deployment runbook see
[tam-discovery/README.md](https://github.com/Huddie/Tam/blob/main/tam-discovery/README.md).

## Publishing from Python

```python
from tam.discovery import upload

result = upload("report.html", title="Earnings Reaction", tags=["earnings"])
print(result.url)
```

Publishes anything satisfying the `Uploadable` protocol directly — a
Plotly `Figure`, or a [`tam.charting`](charting.md) `ChartCall`/
`ChartPipeline` — no need to render to a file first:

```python
from tam.charting import timeseries
from tam.discovery import upload

upload(timeseries(close, title="Price") | timeseries(rsi_14, title="RSI"), title="AAPL + RSI")
```

`upload()` returns an `UploadResult(url, id, version, title, type)`.
Full signature:

```python
upload(
    path_or_figure,             # a path to a .html file, or an Uploadable
    *,
    title: str,
    type: str = "dashboard",    # groups this with other discoveries of the same kind, free-form
    name: str | None = None,    # a stable slug -- publishing again with the same name adds a version, not a new discovery
    project: str | None = None, # assigns to an existing project (slug or id) -- see "Projects" below
    description: str | None = None,
    tags: list[str] | None = None,
    metadata: dict | None = None,
    source_file: str | None = None,   # recorded verbatim as provenance, e.g. the notebook/script that generated this
    token: str | None = None,         # overrides the usual token resolution
    api_url: str | None = None,       # overrides TAM_DISCOVERY_API_URL
    capture_git: bool = True,         # auto-captures commit/branch/repo/dirty-tree from the CURRENT process's cwd
)
```

Re-publishing byte-identical content is cheap, not just correct: the
server hashes the artifact and short-circuits before any upload happens if
it's already seen those exact bytes — either a pure no-op (same discovery,
same content) or a new version row pointed at an already-existing R2
object (different discovery, same content) — never re-uploading the same
bytes twice.

## Projects

An optional, folder-like grouping for discoveries, managed entirely in the
UI at `/settings/projects` — create, rename/edit description, or delete
(soft — see below) a project, each with a `created_by`/`can_manage`
ownership model identical to discoveries themselves. Not everything needs
one: a discovery published without `project=`/`--project` shows up under
an implicit **"General"** bucket in the catalog, filterable the same as
any real project (`project=general`).

```python
from tam.discovery import upload

upload("report.html", title="Earnings Reaction", project="q3-earnings")
```

```bash
upload-discovery report.html --title "Earnings Reaction" --project q3-earnings
upload-discovery list --project q3-earnings
upload-discovery list --project general   # ungrouped discoveries only
```

A few things worth knowing:

- `project` must already exist (create it at `/settings/projects` first) —
  an unknown/typo'd slug is a clear error, not a silently-created stray
  project.
- It only takes effect the **first** time a discovery is created (same as
  `type`) — publishing again under an existing `--name` ignores a changed
  `project`. Move an existing discovery to a different project from the
  catalog's own per-row picker (or the "By project" grouped view) instead.
- The catalog (`/`) has an "All discoveries" / "By project" toggle — the
  latter groups the current page's results into one section per project,
  "General" last.
- Deleting a project in the UI **archives** it (reversible in principle,
  same convention as a discovery's own soft-delete): it disappears from
  the active project picker/list, but any discovery already assigned to it
  keeps pointing at it and keeps showing its name.
- Storage is unaffected either way — R2 objects are content-addressed and
  deduped globally (`artifacts/{sha256}.html`) regardless of which
  project(s) reference them; a project is purely a catalog/D1-level
  grouping, not a storage location.

## Publishing from the CLI

```bash
export TAM_DISCOVERY_API_URL=https://discovery.example.com
uv run upload-discovery login
uv run upload-discovery report.html --title "Smoke test" --tag demo --tag earnings
```

```bash
upload-discovery list -q "earnings" --tag earnings --sort updated
upload-discovery info <name-or-id>
upload-discovery versions <name-or-id>
```

`upload-discovery <path> ...` is shorthand for `upload-discovery publish
<path> ...` — the subcommand name is optional when the first argument
looks like a file, not a flag.

## Authentication

Resolution order (`tam.discovery.auth.resolve_token`), same for the Python
SDK and the CLI:

1. An explicit `token=`/`--token` argument.
2. The `TAM_PAT` environment variable (directly, or via a local `.env`
   file) — the same token also authenticates
   [Data Explorer](tam-data-explorer.md), hence the generic name.
3. A Colab secret named `TAM_PAT`, if running in Colab.
4. Whatever `upload-discovery login` last saved to
   `~/.config/upload-discovery/token`.

Create a token at your Discovery site's `/settings/tokens` page (requires
GitHub login).

## Local development (the Worker itself)

```bash
cd tam-discovery
npm install
npm run dev     # wrangler dev -- local Worker + SPA
npm test        # vitest -- Miniflare-backed D1/R2, no live account needed
```
