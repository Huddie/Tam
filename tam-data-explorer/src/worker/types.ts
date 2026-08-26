export interface Env {
  /** Read-only usage only (this Worker never calls .put()/.delete()) --
   * points at the SAME "tam-data" bucket tam.marketdata already writes to.
   * R2 bucket bindings are account-scoped, not access-key-scoped, so unlike
   * tam-discovery's presigned-upload R2 setup, no S3 API token/secret is
   * needed here at all. */
  DATA: R2Bucket;
  /** This Worker's "DB" binding deliberately points at tam-discovery's OWN
   * D1 database (see wrangler.jsonc) -- personal tokens are unified across
   * both sites, one shared "tokens" table, not a separate copy per site.
   * Catalog metadata (Discovery's own tables) lives in the same database
   * but this Worker never touches those. */
  DB: D1Database;
  ASSETS: Fetcher;
  ACCESS_TEAM_DOMAIN: string;
  ACCESS_AUD: string;
  TOKEN_HMAC_SECRET: string;
  /** Cloudflare account ID -- not secret (same value as root .env's
   * R2_ACCOUNT_ID), needed to call the Temp Credentials API (lib/
   * r2-credentials.ts). */
  R2_ACCOUNT_ID: string;
  /** A "parent" R2 API token (Cloudflare-style Bearer token, NOT the S3
   * access-key/secret pair) with permission to mint temporary credentials
   * on the tam-data bucket -- see README.md's runbook for exactly how to
   * create it. Only ever used server-side to mint short-lived, read-only,
   * per-request credentials (lib/r2-credentials.ts); never handed to a
   * client directly. */
  R2_PARENT_API_TOKEN: string;
  /** The S3-style access key ID of that SAME parent token -- required as
   * the `parentAccessKeyId` field when minting (Cloudflare's API signs the
   * temporary credential against this specific parent). */
  R2_PARENT_ACCESS_KEY_ID: string;
}
