import { findProjectBySlugOrId, newId, nowIso } from "../lib/d1";
import { ApiError } from "../lib/errors";
import { normalizeTag } from "../lib/tags";
import type { Env, ProjectRow } from "../types";

/** GET /api/projects -- every active (non-archived) project, plus how many
 * non-hidden discoveries currently point at it. Archived projects are
 * omitted here (same "hidden means gone from listings, not gone from
 * storage" rule as discoveries.hidden_at) -- a discovery that already
 * belongs to one keeps showing it, it just can't be picked for NEW
 * assignments once it's off this list. */
export async function listProjects(env: Env, user: string): Promise<Response> {
  const { results } = await env.DB.prepare(
    `SELECT p.*, (
       SELECT COUNT(*) FROM discoveries d WHERE d.project_id = p.id AND d.hidden_at IS NULL
     ) AS discovery_count
     FROM projects p WHERE p.archived_at IS NULL ORDER BY p.name COLLATE NOCASE`
  ).all<ProjectRow & { discovery_count: number }>();

  return Response.json({
    projects: results.map((row) => ({
      id: row.id,
      slug: row.slug,
      name: row.name,
      description: row.description,
      created_by: row.created_by,
      created_at: row.created_at,
      updated_at: row.updated_at,
      discovery_count: row.discovery_count,
      can_manage: row.created_by === user,
    })),
  });
}

/** POST /api/projects -- `slug` is normalized the same way tags/types are
 * (lib/tags.ts's normalizeTag()), so "Q3 Earnings" and "q3-earnings" collide
 * on purpose rather than creating two near-duplicate projects. */
export async function createProject(request: Request, env: Env, user: string): Promise<Response> {
  const body = await request.json<{ slug?: string; name?: string; description?: string }>().catch(
    () => ({}) as { slug?: string; name?: string; description?: string }
  );
  const name = body.name?.trim();
  if (!name) throw new ApiError(400, "name is required");
  const slug = normalizeTag(body.slug || name);
  if (!slug) throw new ApiError(400, "slug is empty after normalization");

  const id = newId();
  const now = nowIso();
  try {
    await env.DB.prepare(
      "INSERT INTO projects (id, slug, name, description, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
      .bind(id, slug, name, body.description?.trim() || null, user, now, now)
      .run();
  } catch (error) {
    if (error instanceof Error && error.message.includes("UNIQUE constraint failed")) {
      throw new ApiError(409, `a project with slug ${JSON.stringify(slug)} already exists`);
    }
    throw error;
  }

  return Response.json({ id, slug, name, description: body.description?.trim() || null, created_at: now });
}

async function requireOwnedProject(env: Env, user: string, slugOrId: string): Promise<ProjectRow> {
  const project = await findProjectBySlugOrId(env, slugOrId);
  if (!project) throw new ApiError(404, `no project ${slugOrId}`);
  if (project.created_by !== user) {
    throw new ApiError(403, "only this project's creator can rename or archive it");
  }
  return project;
}

/** PATCH /api/projects/:id -- update name/description, creator only. Slug
 * is immutable once created (same reasoning as a discovery's own slug never
 * changing on rename) -- publish-time `project=<slug>` references and any
 * already-shared link to /?project=<slug> would otherwise silently break. */
export async function updateProject(request: Request, env: Env, user: string, slugOrId: string): Promise<Response> {
  const project = await requireOwnedProject(env, user, slugOrId);
  const body = await request.json<{ name?: string; description?: string }>().catch(
    () => ({}) as { name?: string; description?: string }
  );
  const name = body.name?.trim() || project.name;
  const description = body.description === undefined ? project.description : body.description.trim() || null;

  await env.DB.prepare("UPDATE projects SET name = ?, description = ?, updated_at = ? WHERE id = ?")
    .bind(name, description, nowIso(), project.id)
    .run();

  return Response.json({ id: project.id, slug: project.slug, name, description });
}

/** POST /api/projects/:id/archive -- soft-delete, creator only. Discoveries
 * already assigned to this project keep their `project_id` untouched (see
 * this migration's own comment) -- archiving only removes it from
 * listProjects()'s active list and from being assignable/publishable-into
 * going forward. */
export async function archiveProject(env: Env, user: string, slugOrId: string): Promise<Response> {
  const project = await requireOwnedProject(env, user, slugOrId);
  await env.DB.prepare("UPDATE projects SET archived_at = ? WHERE id = ?").bind(nowIso(), project.id).run();
  return Response.json({ id: project.id, archived: true });
}
