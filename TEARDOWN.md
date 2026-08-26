# Teardown / how to stop paying for any of this

Everything below is either something set up in this session (`tam.marketdata`
-- Cloudflare R2 + Massive flat files) or something visible in this
repo's config from the concurrent `tam.discovery` project (Cloudflare
Workers/D1/Zero Trust). I did not personally set up the Discovery pieces --
I can only see its checked-in config, not its live Cloudflare dashboard
state, so treat that section as "here's what to go check," not a confirmed
bill.

**As of this repo's current state**, based on what's checked in:
`tam-discovery/wrangler.jsonc` still has a placeholder `database_id`
(`<filled-in-by-wrangler-d1-create>`) and no custom-domain `routes` entry --
meaning the Discovery Worker likely has **not** been deployed yet, even
though its GitHub OAuth App and Cloudflare Access team domain already exist
(both real, both in `.env`). Verify this in the Cloudflare dashboard before
assuming it's live either way; don't take my word for it over what you
actually see there.

## What's using what, and roughly what it costs

| Service | Used for | Set up by | Approx. cost | Verify actual cost/usage here |
|---|---|---|---|---|
| Cloudflare account | container for everything below | you (this session) | $0 to have an account | dashboard home |
| Cloudflare R2 -- bucket `tam-data` | `tam.marketdata`'s 1-minute OHLCV Parquet lake | this session | ~$0.015/GB-month storage, ~$0 at current tiny size (a few test rows); real 20-year backfill estimated ~$1-2/month (see MARKETDATA.md) | R2 dashboard -> `tam-data` -> Metrics |
| Cloudflare R2 API tokens (x2: "Tam-Data-R" read-only, + one read-write) | credentials for the bucket above | this session | $0 (tokens themselves are free; they just grant access to the billed bucket) | R2 -> Manage API Tokens |
| Cloudflare R2 -- bucket `tam-discovery-artifacts` | Discovery's published HTML artifacts | tam.discovery (concurrent) | ~$0.015/GB-month, likely near $0 today | R2 dashboard -> `tam-discovery-artifacts` -> Metrics |
| Cloudflare Workers -- `tam-discovery` | Discovery's API + catalog UI | tam.discovery (concurrent) | $0 on Workers Free tier unless request volume is high; **likely not deployed yet** (see note above) | Workers & Pages -> `tam-discovery` (only exists if actually deployed) |
| Cloudflare D1 -- `tam-discovery-db` | Discovery's catalog metadata | tam.discovery (concurrent) | $0 on free tier at this scale; **likely doesn't exist yet** (placeholder `database_id`) | D1 dashboard |
| Cloudflare Zero Trust / Access | gates Discovery's UI behind GitHub login | tam.discovery (concurrent) | $0 on free tier (historically free up to a per-seat limit -- confirm current limit in-dashboard, it's changed before) | Zero Trust -> Settings -> Plan |
| Cloudflare Domain Registration (`tamquant.com`) -- **only if bought through Cloudflare**, not just added as a zone | Discovery's hostname | unknown -- check dashboard | ~$10-20/yr if registered through Cloudflare; $0 extra if it's just a DNS zone for a domain registered elsewhere | dashboard -> Domain Registration (lists it only if bought there) |
| GitHub OAuth App (client ID `Ov23lizw8y1Oe5vOBabl`) | lets Cloudflare Access authenticate via GitHub | tam.discovery (concurrent) | $0, always free | github.com -> Settings -> Developer settings -> OAuth Apps |
| GitHub repository secrets (`CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, any `R2_*`/`MASSIVE_S3_*` you added) | CI deploys / manual ingest workflow | both | $0, secrets themselves are free | repo -> Settings -> Secrets and variables -> Actions |
| GitHub Actions minutes | running `test.yml`/`publish.yml`/`deploy-discovery.yml`/`ingest_minute_bars.yml` | both | $0 if this is a public repo (unlimited); usage-based monthly allowance if private, but these jobs run in seconds -- negligible | repo (or account) -> Settings -> Billing -> Plans and usage |
| **Massive flat-files subscription** | the actual 1-minute market data itself | you | **this is the real recurring cost** -- whatever plan tier you're on; not free-tier-eligible for flat files as far as I found | your Massive account dashboard -> Billing |
| FMP (financialmodelingprep.com) API key | pre-existing, used by `tam.data.providers.FMPProvider` -- unrelated to this session's work | pre-existing | unknown -- FMP has a free tier; I have no visibility into which plan this key is on | financialmodelingprep.com -> your account -> Subscription |

## If you want to stop everything -- do this in order

Cancel the thing that's actually billing you first, then clean up cloud
resources, then revoke credentials, then close accounts last (deleting an
account before revoking its tokens can leave orphaned resources you can no
longer see).

1. **Massive** -- this is your real recurring cost. Log in -> Billing
   / Subscription -> Cancel. Do this first; everything else here is
   either free or a few cents.
2. **FMP** -- check financialmodelingprep.com's account/subscription page;
   cancel if it's on a paid plan (unrelated to marketdata, but you're
   auditing everything).
3. **Cloudflare R2**:
   - Delete the test objects/buckets: R2 dashboard -> `tam-data` -> select
     all objects -> delete -> then delete the bucket itself. Same for
     `tam-discovery-artifacts` if it exists.
   - Revoke both R2 API tokens: R2 -> Manage API Tokens -> delete each one
     (the read-only "Tam-Data-R" one and the read-write one).
4. **Cloudflare Workers/D1** (only if Discovery was actually deployed --
   check first): `npx wrangler delete` from `tam-discovery/` (deletes the
   Worker), then D1 dashboard -> `tam-discovery-db` -> delete database.
5. **Cloudflare Zero Trust / Access**: Zero Trust -> Access -> Applications
   -> delete the Discovery application; Settings -> Authentication -> Login
   methods -> remove the GitHub identity provider.
6. **Cloudflare Domain**: only relevant if `tamquant.com` was registered
   *through* Cloudflare (check Domain Registration page first) --
   registered domains can't be "deleted," only left to expire (don't
   renew) or transferred elsewhere. If it's just a zone for a domain
   registered elsewhere, removing the zone (Websites -> `tamquant.com` ->
   Remove Site) doesn't affect the registration itself.
7. **Close the Cloudflare account**: dashboard -> My Profile -> Account
   Ownership / Configurations -> there's a "Delete Account" flow (only
   available once no zones/domains remain attached, in my experience --
   remove those first per step 6).
8. **GitHub cleanup** (no cost, but for a full audit trail): delete the
   OAuth App (Settings -> Developer settings -> OAuth Apps), remove the
   repository secrets (Settings -> Secrets and variables -> Actions),
   and disable/delete `.github/workflows/ingest_minute_bars.yml` and
   `deploy-discovery.yml` if you don't want them runnable anymore.
9. **Local cleanup**: delete `.env` (holds every credential above in
   plaintext) once you've confirmed everything's revoked --
   `rm .env` -- and delete any local data (`rm -rf data/minute data/eod`)
   if you don't want the downloaded bars sitting on disk either.

## What I can't verify for you

I don't have Cloudflare/Massive/FMP dashboard access -- everything above
about *current* billing status, plan tier, or whether Discovery's Worker is
actually deployed is inferred from this repo's checked-in files, not a live
account check. Before assuming any dollar figure above is accurate, look at
the actual dashboard page linked in each table row.
