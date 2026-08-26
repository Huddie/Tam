# Data Explorer

Browse and export the OHLC minute-bar Parquet lake in R2 (the same bucket
`tam.marketdata` already writes to) from a browser or a script -- paginated
table view of any `.parquet` file, a folder/symbol-year browser, export
(single file or whole folder) as either the original Parquet or a combined
CSV, and full SQL access from Python via a self-service personal token.

This directory is the Cloudflare Worker + React UI. Companion Python client:
`tam/marketdata/explorer_client.py` at the repo root (see the in-app
**API access** page, or `/api-access`, once deployed).

## Local development

```bash
npm install
npm run dev         # wrangler dev -- local Worker + SPA
npm run typecheck
npm run build        # vite build -- produces ./dist, which wrangler deploy serves
```

## Deployment runbook

This reuses most of what's already set up for `tam-discovery` -- same
Cloudflare account, same Zero Trust team, same GitHub identity provider. No
new GitHub OAuth App needed.

### 1. Create the Access Application (browser access)

- Zero Trust -> **Access controls** -> **Applications** -> **Add an
  application** -> **Public DNS**.
- **Application name**: `data`.
- **Domain**: `data.tamquant.com`, path `/*`.
- **Identity providers**: select **GitHub** (the same one already
  configured for Discovery -- nothing to add here).
- **Policies** -> add a policy, action **Allow**, include rule matching
  whoever should be allowed to browse this in a browser (same
  email/org rule you used for Discovery's own policy).
- Save, then find and copy this Application's own **AUD tag** (Overview
  tab) -- it's DIFFERENT from Discovery's AUD tag; this app needs its own.

### 2. Create a second Access Application (script/API bypass)

This is what lets a personal API token (self-service, see step 6) work
without an interactive GitHub login -- same pattern as Discovery's own
`discovery-publish-bypass` app, just for a different path prefix.

- **Add an application** -> **Public DNS** again.
- **Application name**: `data-token-bypass`.
- **Domain**: `data.tamquant.com`, path `/api/token/*` (more specific than
  the `/*` app above -- Access routes a request to whichever Application's
  path is the most specific match).
- **Identity providers**: pick GitHub (required field, irrelevant here
  since Bypass skips identity checks entirely).
- **Policies** -> add a policy, action **Bypass**, include rule
  **Everyone**.
- Save. (No AUD tag needed for this one -- Bypass means Access never issues
  a JWT for these requests at all; our Worker's own personal-token check,
  not Access, is what actually gates `/api/token/*`.)

### 3. Reuse Discovery's D1 database (personal API tokens)

Personal tokens are unified across both sites -- one token works for both
Discovery publishing and Data Explorer/DuckDB access. `wrangler.jsonc`
already points this Worker's `DB` binding at `tam-discovery`'s own
database (same `database_id`) rather than creating a separate one, so
there's nothing to create here -- `tam-discovery`'s own migrations already
own the `tokens` table this Worker reads/writes. Just make sure
`tam-discovery`'s migrations have already been applied (see its own
README) before deploying this Worker.

### 4. Reuse (or create) a "parent" R2 API token (for minting temporary credentials)

This is what lets `connect()` (the Python client's full-SQL-access path)
mint short-lived, read-only credentials per personal-token holder, instead
of ever handing out real R2 account credentials. See
`src/worker/lib/r2-credentials.ts` for exactly how it's used.

Credentials are minted via LOCAL client-side JWT signing (Cloudflare's own
"Authenticate against R2 with temporary credentials" doc, "Locally
(client-side signing)" method) rather than calling Cloudflare's Temporary
Credentials REST API -- that API endpoint consistently rejected every
R2-dashboard-issued parent token we tried with a bare "Authentication
error" (code 10000), reproduced via direct curl across three separate
freshly-created tokens, account ID confirmed correct each time. Local
signing only needs the parent token's S3 **secret access key** (which we
proved works, since it's the same pair used for live R2 ingestion
elsewhere) and never touches `api.cloudflare.com` at all.

A temporary credential can never exceed its parent's own permissions, so
**any existing R2 API token already scoped to Object Read only on
`tam-data`** works as-is -- e.g. the "Tam-Data-R" token already created for
`tam.marketdata` (see root `.env`'s `R2_READONLY_ACCESS_KEY_ID` /
`R2_READONLY_SECRET_ACCESS_KEY`). Reuse that pair; no need to create a new
token. (Here, unlike the REST-API approach, you DO need its Secret Access
Key -- that's the whole point, it's the HMAC-signing key.)

If you'd rather use a dedicated token instead: Cloudflare dashboard ->
**R2** -> **Manage API Tokens** -> **Create API Token** -> **Object Read
only**, scoped to **Apply to specific buckets only** -> `tam-data`.

### 5. Install dependencies and deploy

```bash
npm install
npm run build
npx wrangler deploy
```

### 6. Set Worker secrets

```bash
npx wrangler secret put ACCESS_TEAM_DOMAIN         # tam-data-users.cloudflareaccess.com (same as Discovery's)
npx wrangler secret put ACCESS_AUD                 # the "data" app's own AUD tag from step 1
npx wrangler secret put TOKEN_HMAC_SECRET          # MUST be the EXACT SAME value as tam-discovery's own TOKEN_HMAC_SECRET -- both Workers hash against the one shared "tokens" table, so a mismatched secret here means every token created on either site fails to verify on the other (or, if it's also wrong there, everywhere)
npx wrangler secret put R2_PARENT_ACCESS_KEY_ID       # the "Access Key ID" from step 4
npx wrangler secret put R2_PARENT_SECRET_ACCESS_KEY   # the "Secret Access Key" from step 4
```

### 7. Attach the custom domain

- Cloudflare dashboard -> **Workers & Pages** -> **tam-data-explorer** ->
  **Settings** -> **Domains & Routes** -> **Add** -> **Custom Domain** ->
  `data.tamquant.com`.

### 8. Smoke test

```bash
curl -i https://data.tamquant.com/api/symbols          # no auth -> expect 401
curl -i https://data.tamquant.com/api/token/symbols     # no auth -> expect 401 (bypassed from Access, but still gated by our own token check)
```

Then in a browser: visit `https://data.tamquant.com` -> should redirect to
the same GitHub login Discovery uses, and land on the browse UI. Go to
**Personal tokens** -> create one -> then locally:

```bash
export DATA_EXPLORER_TOKEN=<the token you just created>
uv run python -c "from tam.marketdata.explorer_client import fetch_dataframe; print(fetch_dataframe('AAPL', 2024).head())"
uv run python -c "from tam.marketdata.explorer_client import connect; print(connect().sql(\"SELECT * FROM daily_bars('AAPL') LIMIT 5\").df())"
```

## Updating the site

Either:
- **Automatic**: push to `main` with changes under `tam-data-explorer/**` --
  `.github/workflows/deploy-data-explorer.yml` typechecks, builds, and
  deploys automatically (uses the same `CLOUDFLARE_API_TOKEN`/
  `CLOUDFLARE_ACCOUNT_ID` repo secrets already set up for Discovery).
- **Manual**: `make publish-data-explorer` from the repo root (or
  `make publish-sites` to redeploy both sites at once).
