import { env, SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { hashToken } from "../src/worker/lib/bearer";

async function seedToken(status: "active" | "revoked" = "active"): Promise<string> {
  const token = `tamdisc_test_${status}_${Math.random().toString(36).slice(2)}`;
  const hash = await hashToken(token, env.TOKEN_HMAC_SECRET);
  const now = new Date().toISOString();
  await env.DB.prepare("INSERT INTO tokens (id, user, token_hash, created_at, revoked_at) VALUES (?, ?, ?, ?, ?)")
    .bind(crypto.randomUUID(), "tester@example.com", hash, now, status === "revoked" ? now : null)
    .run();
  return token;
}

describe("bearer auth", () => {
  it("rejects a publish request with no Authorization header", async () => {
    const response = await SELF.fetch("https://discovery.example.com/api/publish/whoami");
    expect(response.status).toBe(401);
  });

  it("rejects a publish request with a token that doesn't exist", async () => {
    const response = await SELF.fetch("https://discovery.example.com/api/publish/whoami", {
      headers: { Authorization: "Bearer tamdisc_not_a_real_token" },
    });
    expect(response.status).toBe(401);
  });

  it("rejects a revoked token", async () => {
    const token = await seedToken("revoked");
    const response = await SELF.fetch("https://discovery.example.com/api/publish/whoami", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.status).toBe(401);
  });

  it("accepts a valid, unrevoked token", async () => {
    const token = await seedToken("active");
    const response = await SELF.fetch("https://discovery.example.com/api/publish/whoami", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ user: "tester@example.com" });
  });
});

describe("Access auth", () => {
  it("rejects a catalog request with no Access assertion", async () => {
    const response = await SELF.fetch("https://discovery.example.com/api/discoveries");
    expect(response.status).toBe(401);
  });

  it("rejects a catalog request with a garbage Access JWT", async () => {
    const response = await SELF.fetch("https://discovery.example.com/api/discoveries", {
      headers: { "Cf-Access-Jwt-Assertion": "not.a.valid.jwt" },
    });
    expect(response.status).toBe(401);
  });

  it("rejects an unauthenticated view request without leaking any artifact content", async () => {
    const response = await SELF.fetch("https://discovery.example.com/d/whatever-id/view");
    expect(response.status).toBe(401);
    const body = await response.text();
    expect(body).not.toContain("<html");
  });
});
