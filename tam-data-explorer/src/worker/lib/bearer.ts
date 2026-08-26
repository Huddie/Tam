import type { Env } from "../types";

const TOKEN_PREFIX = "tamquant_";

/** 32 random bytes, base64url-encoded, tamquant_-prefixed -- identical
 * shape (and, since both Workers bind the SAME "tokens" D1 table and must
 * therefore be given the SAME TOKEN_HMAC_SECRET, functionally
 * interchangeable) with tam-discovery's own copy of this function -- a
 * token created via either site's /settings/tokens page works on both.
 * Shown to the user exactly once at creation time; only its HMAC ever
 * touches D1. */
export function generateToken(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return TOKEN_PREFIX + toBase64Url(bytes);
}

export async function hashToken(token: string, secret: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(token));
  return [...new Uint8Array(signature)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** Verifies a request's personal API token, from EITHER an `Authorization:
 * Bearer <token>` header (curl/Python) or a `?token=<token>` query
 * parameter (DuckDB's httpfs/read_parquet() can't attach custom headers to
 * a plain HTTPS GET, so the token has to travel in the URL for that case).
 * Bumps `last_used_at` on a hit. Returns null (never throws) on anything
 * short of a valid, unrevoked token. */
export async function verifyBearer(request: Request, env: Env): Promise<{ user: string; tokenId: string } | null> {
  const header = request.headers.get("Authorization");
  const headerToken = header?.startsWith("Bearer ") ? header.slice("Bearer ".length).trim() : null;
  const queryToken = new URL(request.url).searchParams.get("token");
  const token = headerToken || queryToken;
  if (!token) return null;

  const hash = await hashToken(token, env.TOKEN_HMAC_SECRET);
  const row = await env.DB.prepare("SELECT id, user FROM tokens WHERE token_hash = ? AND revoked_at IS NULL")
    .bind(hash)
    .first<{ id: string; user: string }>();
  if (!row) return null;

  await env.DB.prepare("UPDATE tokens SET last_used_at = ? WHERE id = ?").bind(new Date().toISOString(), row.id).run();
  return { user: row.user, tokenId: row.id };
}

function toBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
