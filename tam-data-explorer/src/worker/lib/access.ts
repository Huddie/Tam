import { createRemoteJWKSet, jwtVerify } from "jose";
import type { Env } from "../types";

// Cached per-isolate, same reasoning as tam-discovery's identical module:
// createRemoteJWKSet already handles its own re-fetch-on-miss/HTTP caching,
// this just avoids rebuilding the JWKS client on every request.
let cachedJwks: ReturnType<typeof createRemoteJWKSet> | undefined;
let cachedTeamDomain: string | undefined;

function jwks(env: Env) {
  if (!cachedJwks || cachedTeamDomain !== env.ACCESS_TEAM_DOMAIN) {
    cachedJwks = createRemoteJWKSet(new URL(`https://${env.ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs`));
    cachedTeamDomain = env.ACCESS_TEAM_DOMAIN;
  }
  return cachedJwks;
}

/** Independently verifies Cloudflare Access's `Cf-Access-Jwt-Assertion`
 * header against Access's own JWKS + our configured audience -- defense in
 * depth on top of Access's own edge enforcement (same reasoning as
 * tam-discovery/src/worker/lib/access.ts). Returns null (never throws) on
 * any failure.
 *
 * `identity` is `email` for a normal GitHub-login session, or `common_name`
 * (the Service Token's own name) for a Service Token request -- Access
 * issues the same Cf-Access-Jwt-Assertion header either way (see the
 * "API access" page/README for how curl/Python hit this without a browser),
 * it just carries a different identity claim, so both need accepting here. */
export async function verifyAccess(request: Request, env: Env): Promise<{ identity: string } | null> {
  const token = request.headers.get("Cf-Access-Jwt-Assertion");
  if (!token) return null;

  try {
    const { payload } = await jwtVerify(token, jwks(env), { audience: env.ACCESS_AUD });
    const identity = typeof payload.email === "string" ? payload.email : typeof payload.common_name === "string" ? payload.common_name : undefined;
    return identity ? { identity } : null;
  } catch {
    return null;
  }
}
