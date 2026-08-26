import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { getDiscovery, hideDiscovery, listDiscoveries, renameDiscovery } from "../src/worker/routes/discoveries";
import { ApiError } from "../src/worker/lib/errors";

async function seedDiscovery(opts: { id: string; title: string; createdBy: string; createdAt: string }) {
  await env.DB.prepare(
    "INSERT INTO discoveries (id, slug, type, title, created_by, created_at, updated_at, latest_version_id) VALUES (?, NULL, 'dashboard', ?, ?, ?, ?, ?)"
  )
    .bind(opts.id, opts.title, opts.createdBy, opts.createdAt, opts.createdAt, opts.id)
    .run();
}

function patchRequest(title: string) {
  return new Request("https://discovery.example.com/api/discoveries/d-1", {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

describe("renaming and soft-deleting a discovery", () => {
  it("lets the creator rename their own discovery", async () => {
    await seedDiscovery({ id: "d-rename-1", title: "Original title", createdBy: "alice@example.com", createdAt: "2026-01-01T00:00:00.000Z" });

    await renameDiscovery(patchRequest("New title"), env, "alice@example.com", "d-rename-1");

    const detail = await getDiscovery(env, "d-rename-1", "alice@example.com");
    const body = await detail.json<{ title: string }>();
    expect(body.title).toBe("New title");
  });

  it("refuses to rename someone else's discovery", async () => {
    await seedDiscovery({ id: "d-rename-2", title: "Original title", createdBy: "alice@example.com", createdAt: "2026-01-01T00:00:00.000Z" });

    await expect(renameDiscovery(patchRequest("Hijacked"), env, "bob@example.com", "d-rename-2")).rejects.toThrow(ApiError);
  });

  it("hides a discovery from the catalog listing without deleting it", async () => {
    await seedDiscovery({ id: "d-hide-1", title: "Soon hidden", createdBy: "alice@example.com", createdAt: "2026-01-01T00:00:00.000Z" });

    await hideDiscovery(env, "alice@example.com", "d-hide-1");

    const listed = await listDiscoveries(new Request("https://discovery.example.com/api/discoveries"), env, "alice@example.com");
    const listedBody = await listed.json<{ discoveries: Array<{ id: string }> }>();
    expect(listedBody.discoveries.map((d) => d.id)).not.toContain("d-hide-1");

    // Direct access still works -- soft-delete only hides it from the
    // catalog, per the "nothing a URL points to silently disappears" rule.
    const detail = await getDiscovery(env, "d-hide-1", "alice@example.com");
    expect(detail.status).toBe(200);
  });

  it("refuses to hide someone else's discovery", async () => {
    await seedDiscovery({ id: "d-hide-2", title: "Not yours", createdBy: "alice@example.com", createdAt: "2026-01-01T00:00:00.000Z" });

    await expect(hideDiscovery(env, "bob@example.com", "d-hide-2")).rejects.toThrow(ApiError);
  });

  it("reports can_manage=true only for the creator", async () => {
    await seedDiscovery({ id: "d-manage-1", title: "Whose is it", createdBy: "alice@example.com", createdAt: "2026-01-01T00:00:00.000Z" });

    const asCreator = await (await getDiscovery(env, "d-manage-1", "alice@example.com")).json<{ can_manage: boolean }>();
    const asOther = await (await getDiscovery(env, "d-manage-1", "bob@example.com")).json<{ can_manage: boolean }>();

    expect(asCreator.can_manage).toBe(true);
    expect(asOther.can_manage).toBe(false);
  });
});
