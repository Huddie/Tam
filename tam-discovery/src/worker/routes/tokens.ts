import { generateToken, hashToken } from "../lib/bearer";
import { newId, nowIso } from "../lib/d1";
import { ApiError } from "../lib/errors";
import type { Env } from "../types";

/** POST /api/tokens -- the raw token is returned exactly once, here; only
 * its HMAC is ever persisted (see lib/bearer.ts), so it can't be recovered
 * even from a full D1 dump. `name` is required and must be unique for this
 * user (idx_tokens_user_name) -- e.g. "colab"/"laptop" -- so /settings/tokens
 * and `upload-discovery list`-style output can identify which token is
 * which without anyone having to remember creation timestamps. */
export async function createToken(request: Request, env: Env, user: string): Promise<Response> {
  const body = await request.json<{ name?: string }>().catch(() => ({}) as { name?: string });
  const name = body.name?.trim();
  if (!name) throw new ApiError(400, "name is required");

  const token = generateToken();
  const id = newId();
  const now = nowIso();

  try {
    await env.DB.prepare("INSERT INTO tokens (id, user, name, token_hash, created_at) VALUES (?, ?, ?, ?, ?)")
      .bind(id, user, name, await hashToken(token, env.TOKEN_HMAC_SECRET), now)
      .run();
  } catch (error) {
    if (error instanceof Error && error.message.includes("UNIQUE constraint failed")) {
      throw new ApiError(409, `you already have a token named ${JSON.stringify(name)}`);
    }
    throw error;
  }

  return Response.json({ id, name, token, created_at: now });
}

export async function listTokens(env: Env, user: string): Promise<Response> {
  const { results } = await env.DB.prepare(
    "SELECT id, name, created_at, last_used_at, revoked_at FROM tokens WHERE user = ? ORDER BY created_at DESC"
  )
    .bind(user)
    .all();
  return Response.json({ tokens: results });
}

export async function revokeToken(env: Env, user: string, tokenId: string): Promise<Response> {
  const row = await env.DB.prepare("SELECT id FROM tokens WHERE id = ? AND user = ?").bind(tokenId, user).first();
  if (!row) throw new ApiError(404, `no token ${tokenId}`);

  await env.DB.prepare("UPDATE tokens SET revoked_at = ? WHERE id = ?").bind(nowIso(), tokenId).run();
  return new Response(null, { status: 204 });
}
