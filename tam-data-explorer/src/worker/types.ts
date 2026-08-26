export interface Env {
  /** Read-only usage only (this Worker never calls .put()/.delete()) --
   * points at the SAME "tam-data" bucket tam.marketdata already writes to.
   * R2 bucket bindings are account-scoped, not access-key-scoped, so unlike
   * tam-discovery's presigned-upload R2 setup, no S3 API token/secret is
   * needed here at all. */
  DATA: R2Bucket;
  ASSETS: Fetcher;
  ACCESS_TEAM_DOMAIN: string;
  ACCESS_AUD: string;
}
