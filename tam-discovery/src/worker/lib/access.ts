import { createRemoteJWKSet, jwtVerify } from "jose";
import type { Env } from "../types";

// Cached per-isolate, keyed on team domain -- createRemoteJWKSet already
// handles its own re-fetch-on-miss and HTTP caching; this just avoids
// rebuilding the JWKS client on every request within the same isolate.
let cachedJwks: ReturnType<typeof createRemoteJWKSet> | undefined;
let cachedTeamDomain: string | undefined;

function jwks(env: Env) {
  if (!cachedJwks || cachedTeamDomain !== env.ACCESS_TEAM_DOMAIN) {
    cachedJwks = createRemoteJWKSet(new URL(`https://${env.ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs`));
    cachedTeamDomain = env.ACCESS_TEAM_DOMAIN;
  }
  return cachedJwks;
}

/** Independently verifies Cloudflare Access's own `Cf-Access-Jwt-Assertion`
 * header against Access's JWKS + our configured audience -- defense in depth
 * on top of Access's own edge enforcement, not reliance on the informational
 * `Cf-Access-Authenticated-User-Email` header alone. Returns null (never
 * throws) on any failure. */
export async function verifyAccess(request: Request, env: Env): Promise<{ email: string } | null> {
  const token = request.headers.get("Cf-Access-Jwt-Assertion");
  if (!token) return null;

  try {
    const { payload } = await jwtVerify(token, jwks(env), { audience: env.ACCESS_AUD });
    const email = typeof payload.email === "string" ? payload.email : undefined;
    return email ? { email } : null;
  } catch {
    return null;
  }
}
