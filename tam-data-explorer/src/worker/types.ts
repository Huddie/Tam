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
   * R2_ACCOUNT_ID), used as the JWT's audience host + subject when minting
   * temp credentials locally (lib/r2-credentials.ts). */
  R2_ACCOUNT_ID: string;
  /** The S3-style access key ID of a "parent" R2 API token scoped to
   * object-read-only on tam-data -- see README.md's runbook for exactly how
   * to create it. Reused as-is as the temp credential's own access key ID
   * (Cloudflare's local-signing scheme, not ours to choose). */
  R2_PARENT_ACCESS_KEY_ID: string;
  /** That SAME parent token's S3 secret access key -- used ONLY as the
   * HMAC-signing key for the locally-signed JWT that becomes the temp
   * credential's session token (lib/r2-credentials.ts); never handed to a
   * client directly. We use local signing (not Cloudflare's Temporary
   * Credentials REST API) because that API endpoint consistently rejected
   * every R2-dashboard-issued Bearer token we tried with a bare
   * "Authentication error" -- this secret access key is proven to work
   * since it's the same pair used for live R2 ingestion elsewhere. */
  R2_PARENT_SECRET_ACCESS_KEY: string;
}
