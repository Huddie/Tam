import { mintTemporaryCredentials } from "../lib/r2-credentials";
import type { Env } from "../types";

/** POST /api/token/credentials -- mints a short-lived, read-only R2
 * credential for the caller (already authenticated via their own personal
 * token, checked by the caller before this runs). See lib/r2-credentials.ts
 * for what this actually grants and why. */
export async function issueCredentials(request: Request, env: Env): Promise<Response> {
  const body = await request.json<{ ttlSeconds?: number }>().catch(() => ({}) as { ttlSeconds?: number });
  const credentials = await mintTemporaryCredentials(env, body.ttlSeconds);
  return Response.json(credentials);
}
