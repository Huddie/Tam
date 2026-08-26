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

// A stand-in sha256-shaped hash for tests that don't care about a REAL
// digest, just that it's 64 hex chars and (by default) unique per call --
// tests that specifically want two calls to share content pass the same
// value explicitly instead of relying on this default.
function randomHash(): string {
  return crypto.randomUUID().replace(/-/g, "") + crypto.randomUUID().replace(/-/g, "");
}

async function publishFullVersion(discoveryId: string, body: Record<string, unknown>) {
  const contentHash = (body.content_hash as string | undefined) ?? randomHash();
  const createRes = await authed(`/api/publish/discoveries/${discoveryId}/versions`, {
    method: "POST",
    body: JSON.stringify({ ...body, content_hash: contentHash }),
  });
  expect(createRes.status).toBe(200);
  const created = await createRes.json<{
    version_id: string;
    upload_url?: string;
    upload_headers?: Record<string, string>;
    already_exists?: boolean;
    url?: string;
    version?: number;
    title?: string;
  }>();

  // Already deduped (see createVersion()'s own content_hash shortcuts) --
  // nothing to upload or finalize, the response IS the final result.
  if (!created.upload_url) {
    return { created, finalized: { id: created.version_id, url: created.url!, version: created.version!, title: created.title! } };
  }

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

  it("re-publishing identical content under the same discovery does nothing new -- same version, no upload", async () => {
    const discovery = await (
      await authed("/api/publish/discoveries", { method: "POST", body: JSON.stringify({ title: "Dedup test" }) })
    ).json<{ discovery_id: string }>();

    const hash = randomHash();
    const first = await publishFullVersion(discovery.discovery_id, { title: "Dedup test", content_hash: hash });
    expect(first.finalized.version).toBe(1);

    // Second call never even gets an upload_url -- createVersion() should
    // recognize the exact same discovery+hash and short-circuit entirely.
    const secondRes = await authed(`/api/publish/discoveries/${discovery.discovery_id}/versions`, {
      method: "POST",
      body: JSON.stringify({ title: "Dedup test", content_hash: hash }),
    });
    const second = await secondRes.json<{ version_id: string; already_exists?: boolean; version?: number; upload_url?: string }>();

    expect(second.already_exists).toBe(true);
    expect(second.upload_url).toBeUndefined();
    expect(second.version_id).toBe(first.created.version_id);
    expect(second.version).toBe(1);

    // Still exactly one version row for this discovery -- the duplicate
    // call created nothing.
    const { results } = await env.DB.prepare("SELECT id FROM discovery_versions WHERE discovery_id = ?")
      .bind(discovery.discovery_id)
      .all();
    expect(results).toHaveLength(1);
  });

  it("reuses the R2 object across different discoveries with the same content, but still creates a real version row", async () => {
    const discoveryA = await (
      await authed("/api/publish/discoveries", { method: "POST", body: JSON.stringify({ title: "Discovery A" }) })
    ).json<{ discovery_id: string }>();
    const discoveryB = await (
      await authed("/api/publish/discoveries", { method: "POST", body: JSON.stringify({ title: "Discovery B" }) })
    ).json<{ discovery_id: string }>();

    const sharedHash = randomHash();
    const a = await publishFullVersion(discoveryA.discovery_id, { title: "Discovery A", content_hash: sharedHash });

    // B publishes the identical bytes -- createVersion() should see the R2
    // object already exists and finalize immediately, no upload_url.
    const bRes = await authed(`/api/publish/discoveries/${discoveryB.discovery_id}/versions`, {
      method: "POST",
      body: JSON.stringify({ title: "Discovery B", content_hash: sharedHash }),
    });
    const b = await bRes.json<{ version_id: string; already_exists?: boolean; upload_url?: string; version?: number }>();

    expect(b.already_exists).toBe(true);
    expect(b.upload_url).toBeUndefined();
    expect(b.version_id).not.toBe(a.created.version_id); // a genuinely new, distinct version...
    expect(b.version).toBe(1); // ...its own version 1, since it's a different discovery

    const [rowA, rowB] = await Promise.all([
      env.DB.prepare("SELECT r2_key, status FROM discovery_versions WHERE id = ?").bind(a.created.version_id).first<{ r2_key: string; status: string }>(),
      env.DB.prepare("SELECT r2_key, status FROM discovery_versions WHERE id = ?").bind(b.version_id).first<{ r2_key: string; status: string }>(),
    ]);
    expect(rowB!.r2_key).toBe(rowA!.r2_key); // ...backed by the exact same R2 object
    expect(rowB!.status).toBe("finalized");
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
