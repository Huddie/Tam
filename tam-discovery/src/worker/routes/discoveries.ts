import { findDiscoveryBySlugOrId, tagNamesForDiscovery } from "../lib/d1";
import { ApiError } from "../lib/errors";
import { normalizeTag } from "../lib/tags";
import type { DiscoveryRow, Env } from "../types";

const PAGE_SIZE = 50;

/** GET /api/discoveries?q=&tag=&type=&creator=&sort=newest|updated&page= --
 * every filter is optional and combinable (AND'd together); `q` is a plain
 * `LIKE ... COLLATE NOCASE` substring match on title, not FTS5 -- fine at
 * internal-catalog scale. */
export async function listDiscoveries(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const q = url.searchParams.get("q");
  const tag = url.searchParams.get("tag");
  const type = url.searchParams.get("type");
  const creator = url.searchParams.get("creator");
  const sort = url.searchParams.get("sort") === "newest" ? "created_at" : "updated_at";
  const page = Math.max(1, Number(url.searchParams.get("page") ?? "1") || 1);

  const conditions: string[] = [];
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
    }))
  );

  return Response.json({ discoveries, page, hasMore });
}

export async function getDiscovery(env: Env, slugOrId: string): Promise<Response> {
  const discovery = await findDiscoveryBySlugOrId(env, slugOrId);
  if (!discovery) throw new ApiError(404, `no discovery ${slugOrId}`);

  const tags = await tagNamesForDiscovery(env, discovery.id);
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
