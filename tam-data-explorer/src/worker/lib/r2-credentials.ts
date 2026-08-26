import { ApiError } from "./errors";
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
 * the tam-data bucket via Cloudflare's own Temporary Credentials API
 * (POST /accounts/:id/r2/temp-access-credentials) -- this is what lets a
 * personal-token holder run FULL DuckDB glob/multi-file S3 queries (not
 * just single-file HTTP downloads via routes/file.ts's downloadRaw), all
 * without the real, permanent R2 account credentials ever leaving this
 * Worker. Every mint requires an already-verified, currently-valid personal
 * token (lib/bearer.ts, checked by the caller before this runs) -- revoking
 * that token stops NEW credentials from being minted; anything already
 * minted still works until its own short TTL naturally expires (R2 has no
 * separate revocation for already-issued temporary credentials). */
export async function mintTemporaryCredentials(env: Env, requestedTtlSeconds?: number): Promise<TempCredentials> {
  const ttlSeconds = Math.min(Math.max(60, requestedTtlSeconds || DEFAULT_TTL_SECONDS), MAX_TTL_SECONDS);

  const response = await fetch(`https://api.cloudflare.com/client/v4/accounts/${env.R2_ACCOUNT_ID}/r2/temp-access-credentials`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.R2_PARENT_API_TOKEN}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      bucket: BUCKET_NAME,
      parentAccessKeyId: env.R2_PARENT_ACCESS_KEY_ID,
      permission: "object-read-only",
      ttlSeconds,
    }),
  });

  const body = await response.json<{
    success: boolean;
    result?: { accessKeyId: string; secretAccessKey: string; sessionToken: string };
    errors?: Array<{ message: string }>;
  }>();

  if (!response.ok || !body.success || !body.result) {
    const message = body.errors?.map((e) => e.message).join("; ") || `Cloudflare API returned ${response.status}`;
    throw new ApiError(502, `failed to mint temporary R2 credentials: ${message}`);
  }

  return {
    accessKeyId: body.result.accessKeyId,
    secretAccessKey: body.result.secretAccessKey,
    sessionToken: body.result.sessionToken,
    expiresAt: new Date(Date.now() + ttlSeconds * 1000).toISOString(),
    endpoint: `https://${env.R2_ACCOUNT_ID}.r2.cloudflarestorage.com`,
    bucket: BUCKET_NAME,
  };
}
