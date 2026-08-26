# Discovery

A private, GitHub-authenticated catalog for publishing and browsing static
HTML research artifacts -- dashboards (the default `type`), or any other
self-contained `.html` file. Publishing is immutable by default: every
`upload-discovery`/`tam.discovery.upload()` call creates a new version with
its own permanent URL; an optional stable `name` always resolves to whichever
version is latest.

This directory is the Cloudflare Worker (TypeScript, D1, R2, Access) + React
catalog UI that `tam.discovery` (see `/tam/discovery` at the repo root)
talks to. See the repo root for the Python CLI/SDK.

## Local development

```bash
npm install
npm run dev        # wrangler dev -- local Worker + SPA
npm test           # vitest run -- Miniflare-backed D1/R2, no live account needed
npm run build      # vite build -- produces ./dist, which wrangler deploy serves
```

## Deployment runbook -- exact Cloudflare/GitHub steps

Everything below is a one-time, human-only setup (Cloudflare account access,
registering a GitHub OAuth App, and your own domain aren't things this repo's
code can do for you). Follow in order.

### 0. Prerequisites

- A Cloudflare account with a domain already added as a zone. If you don't
  have one yet: **Cloudflare dashboard -> Add a site**, either point an
  existing domain's nameservers at Cloudflare (free, takes a few minutes to
  add, up to ~24h to fully propagate -- Cloudflare shows you the exact
  nameserver values to set at your registrar), or buy a new one directly
  through Cloudflare (**Domain Registration** in the dashboard sidebar, no
  nameserver migration needed since it's already on Cloudflare). Either way
  works the same from here on; substitute your actual domain everywhere
  `example.com` appears below.
- Cloudflare **Zero Trust** enabled for that account (Zero Trust has its own
  free tier, sufficient for this scale) -- visiting **Zero Trust** in the
  dashboard sidebar for the first time prompts you to enable it and pick a
  team name if you haven't already.
- Node.js + `npm` locally; this uses `npx wrangler` throughout, so no global
  install is required.
- Decide the hostname now, e.g. `discovery.example.com` -- needed for step
  2's callback URL.

### 1. Register the GitHub OAuth App

- GitHub -> your account or org -> **Settings -> Developer settings -> OAuth
  Apps -> New OAuth App**.
- *Application name*: `Discovery` (anything).
- *Homepage URL*: `https://discovery.example.com`.
- *Authorization callback URL*:
  `https://<your-team-name>.cloudflareaccess.com/cdn-cgi/access/callback` --
  get the exact `<your-team-name>` value from step 2 below (Cloudflare shows
  it to you when you start adding the GitHub identity provider; open that
  screen first, copy the callback URL it displays, then come back and paste
  it here).
- Save; copy the **Client ID** and generate + copy a **Client Secret** --
  you'll paste both into Cloudflare next.

### 2. Wire GitHub into Cloudflare Access as an identity provider

- Cloudflare dashboard -> **Zero Trust** -> **Settings -> Authentication ->
  Login methods -> Add new -> GitHub**.
- Paste the Client ID/Secret from step 1. Save.
- (If you hadn't set your team name yet, Zero Trust prompts for one on first
  use -- this becomes `<team-name>.cloudflareaccess.com`, used in step 1's
  callback URL and in the Worker's `ACCESS_TEAM_DOMAIN` secret in step 7.)

### 3. Create the Access Application (this is what actually gates the browser UI)

- Zero Trust -> **Access -> Applications -> Add an application ->
  Self-hosted**.
- *Application domain*: `discovery.example.com`, path `/*`.
- *Identity providers*: select GitHub (only).
- *Policies*: Add a policy, e.g. name "Allowed researchers", action
  **Allow**, include rule = **GitHub organization** (enter your org name) --
  or **Emails**/**Individuals** for a specific allowlist instead/in addition.
- Save. Note the **Application Audience (AUD) tag** shown on this
  Application's Overview tab -- you need it verbatim for the Worker's
  `ACCESS_AUD` secret in step 7.
- This Application's `/*` path match is what makes Access intercept the
  whole site. `/api/publish/*` deliberately gets no Access Application of
  its own, so those requests reach the Worker directly -- this is what makes
  headless CLI/Colab publishing possible without an interactive login.

### 4. Install dependencies

```bash
cd tam-discovery
npm install
```

### 5. Create the D1 database

```bash
npx wrangler login
npx wrangler d1 create tam-discovery-db
```

Copy the printed `database_id` into `wrangler.jsonc`'s `d1_databases`
binding block (`binding: "DB"`). Then apply the migration in
`src/worker/migrations/` (referenced via `migrations_dir` in
`wrangler.jsonc`) to both your local dev database and the real remote one --
`wrangler deploy` does NOT do this for you automatically:

```bash
npx wrangler d1 migrations apply tam-discovery-db --local
npx wrangler d1 migrations apply tam-discovery-db --remote
```

### 6. Create the R2 bucket

```bash
npx wrangler r2 bucket create tam-discovery-artifacts
```

Do **not** enable the bucket's public `r2.dev` URL and do **not** bind a
custom domain to it -- the Worker's own binding is the only reader, by
design (see the Security section of the design plan / `src/worker/routes/view.ts`'s
own comments).

Then create an R2 API token scoped to *only* this bucket (Cloudflare
dashboard -> **R2 -> Manage API Tokens -> Create API Token**, permission =
Object Read & Write, scoped to `tam-discovery-artifacts` only) -- needed for
presigned-URL generation in step 7. This is a fresh, separate token from
whatever R2 credentials `tam.marketdata` already uses for its own bucket
(see root `.env`) -- don't reuse those; each bucket gets its own
least-privilege token. `wrangler.jsonc`'s `R2_S3_ENDPOINT` var already has
your account ID filled in (same Cloudflare account as `tam.marketdata`'s R2
usage), so no change needed there.

### 7. Set Worker secrets

```bash
npx wrangler secret put ACCESS_TEAM_DOMAIN     # e.g. "your-team-name.cloudflareaccess.com" from step 2
npx wrangler secret put ACCESS_AUD             # the AUD tag from step 3
npx wrangler secret put TOKEN_HMAC_SECRET      # openssl rand -hex 32
npx wrangler secret put R2_ACCESS_KEY_ID       # from the R2 API token in step 6
npx wrangler secret put R2_SECRET_ACCESS_KEY   # from the R2 API token in step 6
```

### 8. Attach the custom domain and deploy

- `wrangler.jsonc` -> add a `routes` entry for `discovery.example.com/*`, or
  attach it via Cloudflare dashboard -> **Workers & Pages -> tam-discovery ->
  Settings -> Domains & Routes -> Add Custom Domain**. Confirm the DNS
  record it creates is proxied (orange cloud), not DNS-only.

```bash
npm run build
npx wrangler deploy
```

### 9. Smoke test

- Browse to `https://discovery.example.com` -> should redirect to a GitHub
  login prompt (Access) -> after allowing, lands on the (still-empty)
  catalog.
- `https://discovery.example.com/settings/tokens` -> **Create a new token**
  -> copy the shown token (shown once).
- Locally:
  ```bash
  export TAM_DISCOVERY_API_URL=https://discovery.example.com
  uv run upload-discovery login
  # paste the token when prompted
  uv run upload-discovery examples/output/some_report.html --title "Smoke test" --tag demo
  ```
- Confirm it appears in the catalog and the embedded view renders.
- Confirm `curl -i https://discovery.example.com/api/publish/whoami` (no
  auth header) returns a **401 from the Worker**, not an Access GitHub-login
  redirect -- this is the check that `/api/publish/*` is correctly excluded
  from Access.

### 10. (Recommended, ongoing) D1 backups

D1 has scheduled export options (Cloudflare dashboard -> your database ->
Settings) -- enable one, since D1 is the sole source of truth for all
catalog metadata (R2 holds only re-derivable artifact bytes).

### CI: automatic deploys

`.github/workflows/deploy-discovery.yml` runs `npx vitest run` and then
`wrangler deploy` on every push to `main` that touches `tam-discovery/**`.
It needs two repo secrets (**Settings -> Secrets and variables -> Actions**):

- `CLOUDFLARE_API_TOKEN` -- a scoped API token with `Workers Scripts: Edit`
  permission for this account. Create a fresh one for this
  (**Cloudflare dashboard -> My Profile -> API Tokens -> Create Token ->
  Edit Cloudflare Workers** template) rather than reusing the generic
  `CLOUDFLARE_API_TOKEN` already in the root `.env` -- that one was created
  for possible future R2/marketdata API use and its permissions haven't
  been scoped or verified for Workers deploys.
- `CLOUDFLARE_ACCOUNT_ID` -- `65c76cd478aba6575ed57de7d93480ad` (same
  account as `tam.marketdata`'s R2 usage; also visible in the dashboard
  sidebar).

## Updating the site

Either:
- **Automatic**: push to `main` with changes under `tam-discovery/**` --
  the CI workflow above runs tests, then deploys, automatically.
- **Manual**: `make publish-discovery` from the repo root (or
  `make publish-sites` to redeploy Discovery and Data Explorer together).
