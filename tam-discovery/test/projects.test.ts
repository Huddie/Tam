import { env, SELF } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import { hashToken } from "../src/worker/lib/bearer";
import { ApiError } from "../src/worker/lib/errors";
import { archiveProject, createProject, listProjects, updateProject } from "../src/worker/routes/projects";
import { assignProject, getDiscovery, listDiscoveries } from "../src/worker/routes/discoveries";

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

function createRequest(body: unknown) {
  return new Request(`${BASE}/api/projects`, { method: "POST", body: JSON.stringify(body) });
}

function patchRequest(path: string, body: unknown) {
  return new Request(`${BASE}${path}`, { method: "PATCH", body: JSON.stringify(body) });
}

function postRequest(path: string, body: unknown) {
  return new Request(`${BASE}${path}`, { method: "POST", body: JSON.stringify(body) });
}

async function seedDiscovery(opts: { id: string; title: string; createdBy: string; createdAt: string; projectId?: string | null }) {
  await env.DB.prepare(
    "INSERT INTO discoveries (id, slug, type, title, created_by, created_at, updated_at, latest_version_id, project_id) VALUES (?, NULL, 'dashboard', ?, ?, ?, ?, ?, ?)"
  )
    .bind(opts.id, opts.title, opts.createdBy, opts.createdAt, opts.createdAt, opts.id, opts.projectId ?? null)
    .run();
}

describe("creating, listing, and managing projects", () => {
  it("creates a project and normalizes its slug", async () => {
    const res = await createProject(createRequest({ name: "Q3 Earnings" }), env, "alice@example.com");
    const body = await res.json<{ slug: string; name: string }>();

    expect(body.slug).toBe("q3-earnings");
    expect(body.name).toBe("Q3 Earnings");
  });

  it("rejects a duplicate slug", async () => {
    await createProject(createRequest({ name: "Q3 Earnings" }), env, "alice@example.com");

    await expect(createProject(createRequest({ name: "q3 earnings" }), env, "bob@example.com")).rejects.toThrow(ApiError);
  });

  it("lists active projects with can_manage and a live discovery_count", async () => {
    const created = await (
      await createProject(createRequest({ name: "Momentum research" }), env, "alice@example.com")
    ).json<{ id: string }>();
    await seedDiscovery({ id: "d-1", title: "One", createdBy: "alice@example.com", createdAt: "2026-01-01T00:00:00.000Z", projectId: created.id });
    await seedDiscovery({ id: "d-2", title: "Two", createdBy: "alice@example.com", createdAt: "2026-01-01T00:00:00.000Z", projectId: created.id });

    const asCreator = await (await listProjects(env, "alice@example.com")).json<{
      projects: Array<{ id: string; discovery_count: number; can_manage: boolean }>;
    }>();
    const asOther = await (await listProjects(env, "bob@example.com")).json<{ projects: Array<{ id: string; can_manage: boolean }> }>();

    const row = asCreator.projects.find((p) => p.id === created.id);
    expect(row?.discovery_count).toBe(2);
    expect(row?.can_manage).toBe(true);
    expect(asOther.projects.find((p) => p.id === created.id)?.can_manage).toBe(false);
  });

  it("refuses to rename someone else's project", async () => {
    const created = await (await createProject(createRequest({ name: "Owned by Alice" }), env, "alice@example.com")).json<{ id: string }>();

    await expect(
      updateProject(patchRequest(`/api/projects/${created.id}`, { name: "Hijacked" }), env, "bob@example.com", created.id)
    ).rejects.toThrow(ApiError);
  });

  it("lets the creator update name and description", async () => {
    const created = await (await createProject(createRequest({ name: "Draft name" }), env, "alice@example.com")).json<{ id: string }>();

    const updated = await (
      await updateProject(patchRequest(`/api/projects/${created.id}`, { name: "Final name", description: "the goal" }), env, "alice@example.com", created.id)
    ).json<{ name: string; description: string }>();

    expect(updated.name).toBe("Final name");
    expect(updated.description).toBe("the goal");
  });

  it("archiving removes a project from the active list without touching its discoveries' project_id", async () => {
    const created = await (await createProject(createRequest({ name: "Sunset project" }), env, "alice@example.com")).json<{ id: string; slug: string }>();
    await seedDiscovery({ id: "d-archived-1", title: "Still linked", createdBy: "alice@example.com", createdAt: "2026-01-01T00:00:00.000Z", projectId: created.id });

    await archiveProject(env, "alice@example.com", created.id);

    const active = await (await listProjects(env, "alice@example.com")).json<{ projects: Array<{ id: string }> }>();
    expect(active.projects.map((p) => p.id)).not.toContain(created.id);

    const discovery = await (await getDiscovery(env, "d-archived-1", "alice@example.com")).json<{ project: { id: string } | null }>();
    expect(discovery.project?.id).toBe(created.id);
  });

  it("refuses to archive someone else's project", async () => {
    const created = await (await createProject(createRequest({ name: "Owned by Alice" }), env, "alice@example.com")).json<{ id: string }>();

    await expect(archiveProject(env, "bob@example.com", created.id)).rejects.toThrow(ApiError);
  });
});

describe("assigning discoveries to a project", () => {
  it("moves a discovery into a project and back to General", async () => {
    const project = await (await createProject(createRequest({ name: "Basket research" }), env, "alice@example.com")).json<{ slug: string }>();
    await seedDiscovery({ id: "d-move-1", title: "Movable", createdBy: "alice@example.com", createdAt: "2026-01-01T00:00:00.000Z" });

    await assignProject(postRequest("/api/discoveries/d-move-1/project", { project: project.slug }), env, "alice@example.com", "d-move-1");
    const moved = await (await getDiscovery(env, "d-move-1", "alice@example.com")).json<{ project: { slug: string } | null }>();
    expect(moved.project?.slug).toBe(project.slug);

    await assignProject(postRequest("/api/discoveries/d-move-1/project", { project: null }), env, "alice@example.com", "d-move-1");
    const backToGeneral = await (await getDiscovery(env, "d-move-1", "alice@example.com")).json<{ project: unknown }>();
    expect(backToGeneral.project).toBeNull();
  });

  it("rejects assigning to a project that doesn't exist", async () => {
    await seedDiscovery({ id: "d-move-2", title: "Movable", createdBy: "alice@example.com", createdAt: "2026-01-01T00:00:00.000Z" });

    await expect(
      assignProject(postRequest("/api/discoveries/d-move-2/project", { project: "no-such-project" }), env, "alice@example.com", "d-move-2")
    ).rejects.toThrow(ApiError);
  });

  it("refuses to move someone else's discovery", async () => {
    const project = await (await createProject(createRequest({ name: "Someone else's" }), env, "alice@example.com")).json<{ slug: string }>();
    await seedDiscovery({ id: "d-move-3", title: "Not yours", createdBy: "alice@example.com", createdAt: "2026-01-01T00:00:00.000Z" });

    await expect(
      assignProject(postRequest("/api/discoveries/d-move-3/project", { project: project.slug }), env, "bob@example.com", "d-move-3")
    ).rejects.toThrow(ApiError);
  });

  it("filters the catalog listing by project, and by project=general for ungrouped discoveries", async () => {
    const project = await (await createProject(createRequest({ name: "Filtered project" }), env, "alice@example.com")).json<{ id: string; slug: string }>();
    await seedDiscovery({ id: "d-filter-in", title: "In project", createdBy: "alice@example.com", createdAt: "2026-01-01T00:00:00.000Z", projectId: project.id });
    await seedDiscovery({ id: "d-filter-out", title: "Ungrouped", createdBy: "alice@example.com", createdAt: "2026-01-01T00:00:00.000Z" });

    const inProject = await (
      await listDiscoveries(new Request(`${BASE}/api/discoveries?project=${project.slug}`), env, "alice@example.com")
    ).json<{ discoveries: Array<{ id: string }> }>();
    expect(inProject.discoveries.map((d) => d.id)).toEqual(["d-filter-in"]);

    const general = await (
      await listDiscoveries(new Request(`${BASE}/api/discoveries?project=general`), env, "alice@example.com")
    ).json<{ discoveries: Array<{ id: string }> }>();
    expect(general.discoveries.map((d) => d.id)).toContain("d-filter-out");
    expect(general.discoveries.map((d) => d.id)).not.toContain("d-filter-in");
  });
});

describe("publishing directly into a project", () => {
  it("stores project_id when publishing with a valid project slug", async () => {
    const project = await (await createProject(createRequest({ name: "Publish target" }), env, "publisher@example.com")).json<{ id: string; slug: string }>();

    const res = await authed("/api/publish/discoveries", {
      method: "POST",
      body: JSON.stringify({ title: "Grouped report", project: project.slug }),
    });
    expect(res.status).toBe(200);
    const discovery = await res.json<{ discovery_id: string }>();

    const row = await env.DB.prepare("SELECT project_id FROM discoveries WHERE id = ?").bind(discovery.discovery_id).first<{ project_id: string }>();
    expect(row?.project_id).toBe(project.id);
  });

  it("errors instead of silently creating an unknown project", async () => {
    const res = await authed("/api/publish/discoveries", {
      method: "POST",
      body: JSON.stringify({ title: "Orphan report", project: "typo-d-slug" }),
    });

    expect(res.status).toBe(400);
  });

  it("leaves project_id unset when omitted -- goes to General", async () => {
    const res = await authed("/api/publish/discoveries", { method: "POST", body: JSON.stringify({ title: "Ungrouped report" }) });
    const discovery = await res.json<{ discovery_id: string }>();

    const row = await env.DB.prepare("SELECT project_id FROM discoveries WHERE id = ?").bind(discovery.discovery_id).first<{ project_id: string | null }>();
    expect(row?.project_id).toBeNull();
  });
});
