# Data Explorer

Browse and export the OHLC minute-bar Parquet lake in R2 (the same bucket
`tam.marketdata` already writes to) from a browser or a script -- paginated
table view of any `.parquet` file, a folder/symbol-year browser, and
export (single file or whole folder) as either the original Parquet or a
combined CSV.

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

This reuses almost everything already set up for `tam-discovery` --same
Cloudflare account, same Zero Trust team, same GitHub identity provider.
No new GitHub OAuth App, no D1 database, no new R2 bucket/API token (this
Worker only needs a read-only-in-practice binding to the EXISTING `tam-data`
bucket, which doesn't need its own credentials -- see `wrangler.jsonc`).

### 1. Create the Access Application

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

### 2. Install dependencies and deploy

```bash
cd tam-data-explorer
npm install
npm run build
npx wrangler deploy
```

(`npx wrangler login` only if this machine hasn't already authenticated --
if you did that for tam-discovery already, it's still valid here.)

### 3. Set Worker secrets

```bash
npx wrangler secret put ACCESS_TEAM_DOMAIN     # same value as tam-discovery's: tam-data-users.cloudflareaccess.com
npx wrangler secret put ACCESS_AUD             # the AUD tag from step 1 (this app's own, not Discovery's)
```

### 4. Attach the custom domain

- Cloudflare dashboard -> **Workers & Pages** -> **tam-data-explorer** ->
  **Settings** -> **Domains & Routes** -> **Add** -> **Custom Domain** ->
  `data.tamquant.com`.

### 5. Enable script/API access (Service Token)

Browser logins go through GitHub as normal; curl/Python/notebooks need a
**Service Token** instead (Cloudflare's own mechanism for non-interactive
Access-gated API calls -- no separate bearer-token system to build/maintain,
unlike Discovery's publishing tokens, since there's no per-user attribution
need here).

- Zero Trust -> **Access controls** -> **Service Tokens** -> **Create
  Service Token**. Name it (e.g. `data-explorer-scripts`), copy the
  **Client ID** and **Client Secret** (the secret is shown once).
- Back on the `data` Application -> **Policies** -> add a policy, action
  **Service Auth**, include rule **Service Token** -> select the one you
  just created.
- Full curl/Python examples (using this Client ID/Secret): the deployed
  site's own `/api-access` page.

### 6. Smoke test

```bash
curl -i https://data.tamquant.com/api/symbols   # no auth -> expect 401
curl -H "CF-Access-Client-Id: <id>" -H "CF-Access-Client-Secret: <secret>" \
     https://data.tamquant.com/api/symbols       # expect 200 with real symbols
```

Then browse to `https://data.tamquant.com` in a browser -- should redirect
to the same GitHub login Discovery uses, and land on the browse UI.

## Updating the site

Either:
- **Automatic**: push to `main` with changes under `tam-data-explorer/**` --
  `.github/workflows/deploy-data-explorer.yml` typechecks, builds, and
  deploys automatically (uses the same `CLOUDFLARE_API_TOKEN`/
  `CLOUDFLARE_ACCOUNT_ID` repo secrets already set up for Discovery).
- **Manual**: `make publish-data-explorer` from the repo root (or
  `make publish-sites` to redeploy both sites at once).
