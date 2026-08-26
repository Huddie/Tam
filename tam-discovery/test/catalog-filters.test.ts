import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { listDiscoveries } from "../src/worker/routes/discoveries";
import { replaceDiscoveryTagsCache, upsertTagIds } from "../src/worker/lib/d1";

async function seedDiscovery(opts: {
  id: string;
  type: string;
  title: string;
  createdBy: string;
  createdAt: string;
  tags?: string[];
}) {
  await env.DB.prepare(
    "INSERT INTO discoveries (id, slug, type, title, created_by, created_at, updated_at, latest_version_id) VALUES (?, NULL, ?, ?, ?, ?, ?, ?)"
  )
    .bind(opts.id, opts.type, opts.title, opts.createdBy, opts.createdAt, opts.createdAt, opts.id)
    .run();
  if (opts.tags?.length) {
    const tagIds = await upsertTagIds(env, opts.tags);
    await replaceDiscoveryTagsCache(env, opts.id, tagIds);
  }
}

function listRequest(query: string) {
  return new Request(`https://discovery.example.com/api/discoveries${query}`);
}

describe("catalog filtering", () => {
  it("combines type, tag, creator, and text filters (AND, not OR)", async () => {
    await seedDiscovery({
      id: "d-report-alice",
      type: "report",
      title: "Alpha Report",
      createdBy: "alice@example.com",
      createdAt: "2026-01-01T00:00:00.000Z",
      tags: ["earnings"],
    });
    await seedDiscovery({
      id: "d-dashboard-alice",
      type: "dashboard",
      title: "Alpha Dashboard",
      createdBy: "alice@example.com",
      createdAt: "2026-01-02T00:00:00.000Z",
      tags: ["earnings"],
    });
    await seedDiscovery({
      id: "d-report-bob",
      type: "report",
      title: "Beta Report",
      createdBy: "bob@example.com",
      createdAt: "2026-01-03T00:00:00.000Z",
      tags: ["other"],
    });

    const response = await listDiscoveries(listRequest("?type=report&tag=earnings&creator=alice@example.com"), env);
    const body = await response.json<{ discoveries: Array<{ id: string }> }>();

    expect(body.discoveries.map((d) => d.id)).toEqual(["d-report-alice"]);
  });

  it("filters by a free-text substring, case-insensitively", async () => {
    await seedDiscovery({
      id: "d-text-1",
      type: "dashboard",
      title: "Quarterly Earnings Overview",
      createdBy: "x@example.com",
      createdAt: "2026-02-01T00:00:00.000Z",
    });
    await seedDiscovery({
      id: "d-text-2",
      type: "dashboard",
      title: "Unrelated Thing",
      createdBy: "x@example.com",
      createdAt: "2026-02-02T00:00:00.000Z",
    });

    const response = await listDiscoveries(listRequest("?q=earnings"), env);
    const body = await response.json<{ discoveries: Array<{ id: string }> }>();

    expect(body.discoveries.map((d) => d.id)).toEqual(["d-text-1"]);
  });

  it("sorts by newest (created_at) vs. updated (updated_at) as requested", async () => {
    await seedDiscovery({ id: "d-sort-old", type: "x", title: "Old", createdBy: "x@example.com", createdAt: "2026-01-01T00:00:00.000Z" });
    await seedDiscovery({ id: "d-sort-new", type: "x", title: "New", createdBy: "x@example.com", createdAt: "2026-03-01T00:00:00.000Z" });
    // Make "Old" the most recently UPDATED despite being created first.
    await env.DB.prepare("UPDATE discoveries SET updated_at = ? WHERE id = ?").bind("2026-04-01T00:00:00.000Z", "d-sort-old").run();

    const byNewest = await (await listDiscoveries(listRequest("?sort=newest"), env)).json<{ discoveries: Array<{ id: string }> }>();
    const byUpdated = await (await listDiscoveries(listRequest("?sort=updated"), env)).json<{ discoveries: Array<{ id: string }> }>();

    expect(byNewest.discoveries[0].id).toBe("d-sort-new");
    expect(byUpdated.discoveries[0].id).toBe("d-sort-old");
  });
});
