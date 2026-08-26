import { SignJWT } from "jose";
import type { Env } from "../types";

const BUCKET_NAME = "tam-data";
const DEFAULT_TTL_SECONDS = 900; // 15 minutes
const MAX_TTL_SECONDS = 3600; // 1 hour -- Cloudflare's own docs recommend "the shortest TTL that fits your use case"; this is a server-side ceiling regardless of what a caller requests.

export interface TempCredentials {
  accessKeyId: string;
  secretAccessKey: string;
  sessionToken: string;
  expiresAt: string;
  endpoint: string;
  bucket: string;
}

/** Mints a short-lived, READ-ONLY, real R2-native S3 credential scoped to
 * the tam-data bucket via LOCAL client-side JWT signing (Cloudflare's
 * "Locally (client-side signing)" method, see R2's own "Authenticate
 * against R2 with temporary credentials" doc) rather than calling
 * Cloudflare's Temporary Credentials API (POST /accounts/:id/r2/
 * temp-access-credentials) -- that REST endpoint consistently rejected
 * every R2-dashboard-issued parent token we tried with a bare
 * "Authentication error" (code 10000), reproduced identically via direct
 * curl across three separate freshly-created tokens, so we sidestepped it
 * entirely. Local signing only needs the parent's S3 secret access key
 * (proven working -- it's the same pair already used for live R2
 * ingestion), never touches api.cloudflare.com, and avoids a per-mint
 * network round-trip. This is what lets a personal-token holder run FULL
 * DuckDB glob/multi-file S3 queries (not just single-file HTTP downloads
 * via routes/file.ts's downloadRaw), all without the real, permanent R2
 * account credentials ever leaving this Worker. Every mint requires an
 * already-verified, currently-valid personal token (lib/bearer.ts, checked
 * by the caller before this runs) -- revoking that token stops NEW
 * credentials from being minted; anything already minted still works
 * until its own short TTL naturally expires (R2 has no separate
 * revocation for already-issued temporary credentials). */
export async function mintTemporaryCredentials(env: Env, requestedTtlSeconds?: number): Promise<TempCredentials> {
  const ttlSeconds = Math.min(Math.max(60, requestedTtlSeconds || DEFAULT_TTL_SECONDS), MAX_TTL_SECONDS);
  const endpoint = `https://${env.R2_ACCOUNT_ID}.r2.cloudflarestorage.com`;

  const jwt = await new SignJWT({ bucket: BUCKET_NAME, scope: "object-read-only" })
    .setProtectedHeader({ alg: "HS256", typ: "JWT" })
    .setSubject(env.R2_ACCOUNT_ID)
    .setIssuer(env.R2_PARENT_ACCESS_KEY_ID)
    .setAudience(new URL(endpoint).host)
    .setIssuedAt()
    .setExpirationTime(`${ttlSeconds}s`)
    .sign(new TextEncoder().encode(env.R2_PARENT_SECRET_ACCESS_KEY));

  // The temporary secret access key is the SHA-256 hex digest of the signed
  // JWT; the session token is base64("jwt/" + signed JWT) -- both exact
  // formulas from Cloudflare's own local-signing example, not ours to
  // choose (R2 validates the JWT signature server-side on every request).
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(jwt));
  const secretAccessKey = Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");

  return {
    accessKeyId: env.R2_PARENT_ACCESS_KEY_ID,
    secretAccessKey,
    sessionToken: btoa(`jwt/${jwt}`),
    expiresAt: new Date(Date.now() + ttlSeconds * 1000).toISOString(),
    endpoint,
    bucket: BUCKET_NAME,
  };
}
