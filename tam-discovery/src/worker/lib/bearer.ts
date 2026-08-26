import type { Env } from "../types";

const TOKEN_PREFIX = "tamdisc_";

/** 32 random bytes, base64url-encoded, tamdisc_-prefixed -- shown to the user
 * exactly once at creation time; only its HMAC ever touches D1. */
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

/** Verifies the request's `Authorization: Bearer <token>` header against
 * `tokens.token_hash`, bumping `last_used_at` on a hit. Returns null (never
 * throws) on anything short of a valid, unrevoked token -- callers turn that
 * into a 401 without leaking which part of the check failed. */
export async function verifyBearer(request: Request, env: Env): Promise<{ user: string; tokenId: string } | null> {
  const header = request.headers.get("Authorization");
  if (!header?.startsWith("Bearer ")) return null;
  const token = header.slice("Bearer ".length).trim();
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
