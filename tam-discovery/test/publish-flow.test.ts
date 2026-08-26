import { env, SELF } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import { hashToken } from "../src/worker/lib/bearer";
import { resolveVersionTarget, upsertTagIds } from "../src/worker/lib/d1";

const BASE = "https://discovery.example.com";
let token: string;

beforeEach(async () => {
  token = `tamdisc_${crypto.randomUUID()}`;
  await env.DB.prepare("INSERT INTO tokens (id, user, token_hash, created_at) VALUES (?, ?, ?, ?)")
    .bind(crypto.randomUUID(), "publisher@example.com", await hashToken(token, env.TOKEN_HMAC_SECRET), new Date().toISOString())
    .run();
});

function authed(path: string, init: RequestInit = {}) {
  return SELF.fetch(`${BASE}${path}`, {
    ...init,
    headers: { ...init.headers, Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  });
}

async function publishFullVersion(discoveryId: string, body: Record<string, unknown>) {
  const createRes = await authed(`/api/publish/discoveries/${discoveryId}/versions`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  expect(createRes.status).toBe(200);
  const created = await createRes.json<{ version_id: string; upload_url: string; upload_headers: Record<string, string> }>();

  // The presigned PUT URL points at R2's real S3-compatible endpoint, which
  // Miniflare doesn't serve over HTTP -- simulate "the CLI's PUT already
  // landed" by writing straight through the R2 binding instead, keyed the
  // same way createVersion() computed it.
  const row = await env.DB.prepare("SELECT r2_key FROM discovery_versions WHERE id = ?")
    .bind(created.version_id)
    .first<{ r2_key: string }>();
  await env.ARTIFACTS.put(row!.r2_key, "<html>test artifact</html>");

  const finalizeRes = await authed(`/api/publish/discoveries/${discoveryId}/versions/${created.version_id}/finalize`, {
    method: "POST",
    body: JSON.stringify({ size_bytes: 26 }),
  });
  expect(finalizeRes.status).toBe(200);
  const finalized = await finalizeRes.json<{ id: string; url: string; version: number; title: string }>();
  return { created, finalized };
}

describe("two-phase publish flow", () => {
  it("creates a discovery, publishes a version, and returns a stable per-version url", async () => {
    const createDiscoveryRes = await authed("/api/publish/discoveries", {
      method: "POST",
      body: JSON.stringify({ title: "Q3 Report", type: "report" }),
    });
    expect(createDiscoveryRes.status).toBe(200);
    const discovery = await createDiscoveryRes.json<{ discovery_id: string; type: string }>();
    expect(discovery.type).toBe("report");

    const { finalized } = await publishFullVersion(discovery.discovery_id, {
      title: "Q3 Report",
      tags: ["q3", "finance"],
      metadata: { rows: 42, source: "notebook" },
    });

    expect(finalized.version).toBe(1);
    expect(finalized.url).toBe(`${BASE}/d/${finalized.id}`);
  });

  it("round-trips arbitrary metadata verbatim", async () => {
    const discovery = await (
      await authed("/api/publish/discoveries", { method: "POST", body: JSON.stringify({ title: "Meta test" }) })
    ).json<{ discovery_id: string }>();

    const metadata = { parameters: { window: 20 }, nested: { deeply: ["a", "b"] } };
    const { created } = await publishFullVersion(discovery.discovery_id, { title: "Meta test", metadata });

    const row = await env.DB.prepare("SELECT metadata_json FROM discovery_versions WHERE id = ?")
      .bind(created.version_id)
      .first<{ metadata_json: string }>();
    expect(JSON.parse(row!.metadata_json)).toEqual(metadata);
  });

  it("keeps an older version's own row unchanged after a newer version is published (immutability)", async () => {
    const discovery = await (
      await authed("/api/publish/discoveries", {
        method: "POST",
        body: JSON.stringify({ title: "Immutable test", name: "immutable-test" }),
      })
    ).json<{ discovery_id: string }>();

    const v1 = await publishFullVersion(discovery.discovery_id, { title: "V1" });
    const v1Before = await env.DB.prepare("SELECT * FROM discovery_versions WHERE id = ?").bind(v1.created.version_id).first();

    const v2 = await publishFullVersion(discovery.discovery_id, { title: "V2" });
    expect(v2.finalized.version).toBe(2);

    const v1After = await env.DB.prepare("SELECT * FROM discovery_versions WHERE id = ?").bind(v1.created.version_id).first();
    expect(v1After).toEqual(v1Before);

    // The stable name always resolves to the LATEST version...
    const byName = await resolveVersionTarget(env, "immutable-test");
    expect(byName?.version.id).toBe(v2.created.version_id);
    // ...but the first version's own id keeps pointing at v1, forever.
    const byOldVersionId = await resolveVersionTarget(env, v1.created.version_id);
    expect(byOldVersionId?.version.id).toBe(v1.created.version_id);
  });

  it("reuses the same discovery when publishing again under the same name", async () => {
    const first = await (
      await authed("/api/publish/discoveries", { method: "POST", body: JSON.stringify({ title: "T", name: "same-name" }) })
    ).json<{ discovery_id: string }>();
    const second = await (
      await authed("/api/publish/discoveries", { method: "POST", body: JSON.stringify({ title: "T2", name: "Same Name" }) })
    ).json<{ discovery_id: string }>();

    expect(second.discovery_id).toBe(first.discovery_id);
  });
});

describe("tag normalization + dedupe", () => {
  it("collapses spelling variants of the same tag to a single row", async () => {
    const ids = await upsertTagIds(env, ["After Hours", "after-hours", "after_hours", "  AFTER HOURS  "]);
    expect(new Set(ids).size).toBe(1);

    const { results } = await env.DB.prepare("SELECT name FROM tags WHERE name = 'after-hours'").all();
    expect(results).toHaveLength(1);
  });

  it("rejects a tag that normalizes to empty", async () => {
    await expect(upsertTagIds(env, ["---"])).rejects.toThrow(/empty after normalization/);
  });
});
