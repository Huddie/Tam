import { findDiscoveryBySlugOrId, findProjectBySlugOrId, nowIso, tagNamesForDiscovery } from "../lib/d1";
import { ApiError } from "../lib/errors";
import { normalizeTag } from "../lib/tags";
import type { DiscoveryRow, Env, ProjectRow } from "../types";

const PAGE_SIZE = 50;

/** `discoveries.project_id` -> `{id, slug, name}` (or `null`, meaning
 * "General") for every row in one batch -- avoids an N+1 project lookup per
 * listing page. Archived projects are looked up too (not filtered out): a
 * discovery pointing at one still shows its name, per projects.ts's own
 * "archiving doesn't touch project_id" comment. */
async function projectsForDiscoveries(
  env: Env,
  rows: DiscoveryRow[]
): Promise<Map<string, { id: string; slug: string; name: string }>> {
  const ids = [...new Set(rows.map((row) => row.project_id).filter((id): id is string => id !== null))];
  if (!ids.length) return new Map();

  const { results } = await env.DB.prepare(
    `SELECT id, slug, name FROM projects WHERE id IN (${ids.map(() => "?").join(",")})`
  )
    .bind(...ids)
    .all<Pick<ProjectRow, "id" | "slug" | "name">>();
  return new Map(results.map((row) => [row.id, row]));
}

/** GET /api/discoveries?q=&tag=&type=&creator=&project=&sort=newest|updated&page=
 * -- every filter is optional and combinable (AND'd together); `q` is a
 * plain `LIKE ... COLLATE NOCASE` substring match on title, not FTS5 -- fine
 * at internal-catalog scale. Hidden (soft-deleted) discoveries never appear
 * here, regardless of other filters -- see hideDiscovery() below; their
 * permalink/version URLs still resolve directly, just not through the
 * catalog listing. `project=general` explicitly lists ungrouped discoveries
 * (`project_id IS NULL`); any other `project` value is resolved as a
 * slug/id via findProjectBySlugOrId(). */
export async function listDiscoveries(request: Request, env: Env, user: string): Promise<Response> {
  const url = new URL(request.url);
  const q = url.searchParams.get("q");
  const tag = url.searchParams.get("tag");
  const type = url.searchParams.get("type");
  const creator = url.searchParams.get("creator");
  const project = url.searchParams.get("project");
  const sort = url.searchParams.get("sort") === "newest" ? "created_at" : "updated_at";
  const page = Math.max(1, Number(url.searchParams.get("page") ?? "1") || 1);

  const conditions: string[] = ["d.hidden_at IS NULL"];
  const bindings: unknown[] = [];
  if (q) {
    conditions.push("d.title LIKE ? COLLATE NOCASE");
    bindings.push(`%${q}%`);
  }
  if (type) {
    conditions.push("d.type = ?");
    bindings.push(normalizeTag(type));
  }
  if (creator) {
    conditions.push("d.created_by = ?");
    bindings.push(creator);
  }
  if (tag) {
    conditions.push(
      "d.id IN (SELECT dt.discovery_id FROM discovery_tags dt JOIN tags t ON t.id = dt.tag_id WHERE t.name = ?)"
    );
    bindings.push(normalizeTag(tag));
  }
  if (project === "general") {
    conditions.push("d.project_id IS NULL");
  } else if (project) {
    const projectRow = await findProjectBySlugOrId(env, project);
    conditions.push("d.project_id = ?");
    bindings.push(projectRow?.id ?? "__no_such_project__");
  }
  const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";

  const { results } = await env.DB.prepare(
    `SELECT d.* FROM discoveries d ${where} ORDER BY d.${sort} DESC LIMIT ? OFFSET ?`
  )
    // Fetch one extra row (PAGE_SIZE + 1) purely to detect "is there a next
    // page" without a separate COUNT(*) query -- trimmed back off below
    // before returning.
    .bind(...bindings, PAGE_SIZE + 1, (page - 1) * PAGE_SIZE)
    .all<DiscoveryRow>();

  const hasMore = results.length > PAGE_SIZE;
  const pageRows = hasMore ? results.slice(0, PAGE_SIZE) : results;
  const projectsById = await projectsForDiscoveries(env, pageRows);

  const discoveries = await Promise.all(
    pageRows.map(async (row) => ({
      id: row.id,
      name: row.slug ?? row.id,
      type: row.type,
      title: row.title,
      created_by: row.created_by,
      created_at: row.created_at,
      updated_at: row.updated_at,
      tags: await tagNamesForDiscovery(env, row.id),
      project: row.project_id ? (projectsById.get(row.project_id) ?? null) : null,
      can_manage: row.created_by === user,
    }))
  );

  return Response.json({ discoveries, page, hasMore });
}

export async function getDiscovery(env: Env, slugOrId: string, user: string): Promise<Response> {
  const discovery = await findDiscoveryBySlugOrId(env, slugOrId);
  if (!discovery) throw new ApiError(404, `no discovery ${slugOrId}`);

  const tags = await tagNamesForDiscovery(env, discovery.id);
  const projectsById = await projectsForDiscoveries(env, [discovery]);
  return Response.json({
    id: discovery.id,
    name: discovery.slug ?? discovery.id,
    type: discovery.type,
    title: discovery.title,
    created_by: discovery.created_by,
    created_at: discovery.created_at,
    updated_at: discovery.updated_at,
    latest_version_id: discovery.latest_version_id,
    tags,
    project: discovery.project_id ? (projectsById.get(discovery.project_id) ?? null) : null,
    // Lets the catalog/detail UI decide whether to show the rename/delete
    // menu at all, without the client needing to know or trust its own
    // idea of "who am I" -- this reflects the server's own Access-verified
    // identity for this request.
    can_manage: discovery.created_by === user,
  });
}

export async function getVersions(env: Env, slugOrId: string): Promise<Response> {
  const discovery = await findDiscoveryBySlugOrId(env, slugOrId);
  if (!discovery) throw new ApiError(404, `no discovery ${slugOrId}`);

  const { results } = await env.DB.prepare(
    `SELECT id, version_number, title, description, uploaded_by, created_at, git_commit, git_branch, git_repo, git_dirty
     FROM discovery_versions WHERE discovery_id = ? AND status = 'finalized' ORDER BY version_number DESC`
  )
    .bind(discovery.id)
    .all();

  return Response.json({ versions: results });
}

async function requireOwnedDiscovery(env: Env, user: string, slugOrId: string): Promise<DiscoveryRow> {
  const discovery = await findDiscoveryBySlugOrId(env, slugOrId);
  if (!discovery) throw new ApiError(404, `no discovery ${slugOrId}`);
  if (discovery.created_by !== user) {
    throw new ApiError(403, "only this discovery's creator can rename or delete it");
  }
  return discovery;
}

/** PATCH /api/discoveries/:id -- rename (retitle) a discovery, creator
 * only. Renaming changes the display title but NOT its permalink/slug or
 * any already-finalized version's own `title` field -- those stay exactly
 * as published, since a version's own metadata is otherwise immutable by
 * design (see the original plan's "immutable per-version URLs" decision).
 * This only touches the denormalized `discoveries.title` copy used by the
 * catalog listing/detail header. */
export async function renameDiscovery(request: Request, env: Env, user: string, slugOrId: string): Promise<Response> {
  const discovery = await requireOwnedDiscovery(env, user, slugOrId);
  const body = await request.json<{ title?: string }>().catch(() => ({}) as { title?: string });
  const title = body.title?.trim();
  if (!title) throw new ApiError(400, "title is required");

  await env.DB.prepare("UPDATE discoveries SET title = ?, updated_at = ? WHERE id = ?")
    .bind(title, nowIso(), discovery.id)
    .run();

  return Response.json({ id: discovery.id, title });
}

/** POST /api/discoveries/:id/project -- move a discovery to a different
 * project, or back to "General" with `project: null`/omitted, creator
 * only. `project` is a slug/id resolved the same way publish-time
 * `project=` is (see routes/publish.ts's createDiscovery()) -- must already
 * exist and not be archived, same "error, don't silently create/attach to
 * a typo'd slug" rule. */
export async function assignProject(request: Request, env: Env, user: string, slugOrId: string): Promise<Response> {
  const discovery = await requireOwnedDiscovery(env, user, slugOrId);
  const body = await request.json<{ project?: string | null }>().catch(() => ({}) as { project?: string | null });

  let projectId: string | null = null;
  if (body.project) {
    const project = await findProjectBySlugOrId(env, body.project);
    if (!project || project.archived_at) {
      throw new ApiError(
        400,
        `project ${JSON.stringify(body.project)} not found -- create it first at /settings/projects`
      );
    }
    projectId = project.id;
  }

  await env.DB.prepare("UPDATE discoveries SET project_id = ?, updated_at = ? WHERE id = ?")
    .bind(projectId, nowIso(), discovery.id)
    .run();

  return Response.json({ id: discovery.id, project_id: projectId });
}

/** POST /api/discoveries/:id/hide -- soft-delete, creator only. Removes it
 * from the catalog listing (see listDiscoveries()'s hidden_at filter above)
 * without touching D1 rows or R2 bytes -- anyone who already has this
 * discovery's permalink or a specific version's URL can still open it,
 * matching the original plan's "nothing a URL points to silently
 * disappears" guarantee. Reversible in principle (clearing hidden_at)
 * even though there's no UI for that today. */
export async function hideDiscovery(env: Env, user: string, slugOrId: string): Promise<Response> {
  const discovery = await requireOwnedDiscovery(env, user, slugOrId);
  await env.DB.prepare("UPDATE discoveries SET hidden_at = ? WHERE id = ?").bind(nowIso(), discovery.id).run();
  return Response.json({ id: discovery.id, hidden: true });
}
