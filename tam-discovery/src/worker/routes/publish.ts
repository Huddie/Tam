import { ApiError } from "../lib/errors";
import {
  findDiscoveryBySlugOrId,
  linkVersionTags,
  newId,
  nowIso,
  replaceDiscoveryTagsCache,
  tagNamesForDiscovery,
  upsertTagIds,
} from "../lib/d1";
import { createPresignedUploadUrl } from "../lib/r2";
import { normalizeTag } from "../lib/tags";
import type { DiscoveryRow, Env, VersionRow } from "../types";

export async function whoami(user: string): Promise<Response> {
  return Response.json({ user });
}

/** POST /api/publish/discoveries -- resolve-or-create by `name` (the stable
 * slug), or create a brand-new unaliased discovery if `name` is omitted.
 * Doesn't create a version yet -- that's the next call -- so this alone
 * never appears in the catalog with nothing to show. */
export async function createDiscovery(request: Request, env: Env, user: string): Promise<Response> {
  const body = await request.json<{ title?: string; type?: string; name?: string }>();
  if (!body.title) throw new ApiError(400, "title is required");

  const type = normalizeTag(body.type || "dashboard");
  if (!type) throw new ApiError(400, "type is empty after normalization");

  if (body.name) {
    const slug = normalizeTag(body.name);
    if (!slug) throw new ApiError(400, "name is empty after normalization");

    const existing = await env.DB.prepare("SELECT * FROM discoveries WHERE slug = ?").bind(slug).first<DiscoveryRow>();
    if (existing) {
      return Response.json({ discovery_id: existing.id, slug: existing.slug, type: existing.type, title: existing.title });
    }

    const id = newId();
    const now = nowIso();
    // latest_version_id is NOT NULL, but no version exists yet at
    // create-discovery time -- pointed at itself as a placeholder;
    // create_version()/finalize() always overwrite it before any reader
    // (catalog list, /d/:id) can observe this row.
    await env.DB.prepare(
      "INSERT INTO discoveries (id, slug, type, title, created_by, created_at, updated_at, latest_version_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
      .bind(id, slug, type, body.title, user, now, now, id)
      .run();
    return Response.json({ discovery_id: id, slug, type, title: body.title });
  }

  const id = newId();
  const now = nowIso();
  await env.DB.prepare(
    "INSERT INTO discoveries (id, slug, type, title, created_by, created_at, updated_at, latest_version_id) VALUES (?, NULL, ?, ?, ?, ?, ?, ?)"
  )
    .bind(id, type, body.title, user, now, now, id)
    .run();
  return Response.json({ discovery_id: id, slug: null, type, title: body.title });
}

/** POST /api/publish/discoveries/:id/versions -- phase 1 of publishing:
 * insert a `status='pending'` row and hand back a presigned R2 PUT URL. The
 * version isn't visible as "latest" (or at all, from the catalog's point of
 * view) until finalize() promotes it -- a crash between here and finalize
 * just leaves an orphaned pending row, never a broken "latest version". */
export async function createVersion(request: Request, env: Env, user: string, discoveryId: string): Promise<Response> {
  const discovery = await findDiscoveryBySlugOrId(env, discoveryId);
  if (!discovery) throw new ApiError(404, `no discovery ${discoveryId}`);

  const body = await request.json<{
    title?: string;
    description?: string;
    tags?: string[];
    metadata?: Record<string, unknown>;
    source_file?: string;
    git_commit?: string;
    git_branch?: string;
    git_repo?: string;
    git_dirty?: boolean;
  }>();
  if (!body.title) throw new ApiError(400, "title is required");

  const versionNumberRow = await env.DB.prepare(
    "SELECT COALESCE(MAX(version_number), 0) + 1 AS next FROM discovery_versions WHERE discovery_id = ?"
  )
    .bind(discovery.id)
    .first<{ next: number }>();
  const versionNumber = versionNumberRow?.next ?? 1;

  const id = newId();
  const now = nowIso();
  const r2Key = `discoveries/${discovery.id}/${id}/index.html`;

  await env.DB.prepare(
    `INSERT INTO discovery_versions
      (id, discovery_id, version_number, status, title, description, uploaded_by, created_at,
       source_file, git_commit, git_branch, git_repo, git_dirty, generated_at, r2_key, metadata_json)
     VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
  )
    .bind(
      id,
      discovery.id,
      versionNumber,
      body.title,
      body.description ?? null,
      user,
      now,
      body.source_file ?? null,
      body.git_commit ?? null,
      body.git_branch ?? null,
      body.git_repo ?? null,
      body.git_dirty === undefined ? null : body.git_dirty ? 1 : 0,
      now,
      r2Key,
      JSON.stringify(body.metadata ?? {})
    )
    .run();

  if (body.tags?.length) {
    const tagIds = await upsertTagIds(env, body.tags);
    await linkVersionTags(env, id, tagIds);
  }

  const uploadUrl = await createPresignedUploadUrl(env, r2Key);
  return Response.json({ version_id: id, upload_url: uploadUrl, upload_headers: {} });
}

/** POST /api/publish/discoveries/:id/versions/:vid/finalize -- phase 2:
 * confirm the object actually landed in R2 (never trust the client's own
 * say-so), flip status to 'finalized', and promote it to latest. */
export async function finalizeVersion(
  request: Request,
  env: Env,
  _user: string,
  discoveryId: string,
  versionId: string
): Promise<Response> {
  const version = await env.DB.prepare("SELECT * FROM discovery_versions WHERE id = ? AND discovery_id = ?")
    .bind(versionId, discoveryId)
    .first<VersionRow>();
  if (!version) throw new ApiError(404, `no pending version ${versionId} on discovery ${discoveryId}`);
  if (version.status === "finalized") throw new ApiError(409, `version ${versionId} is already finalized`);

  const object = await env.ARTIFACTS.head(version.r2_key);
  if (!object) throw new ApiError(409, "artifact was not found in storage -- the PUT to upload_url may not have completed");

  const body = await request.json<{ size_bytes?: number }>();
  const now = nowIso();

  await env.DB.prepare("UPDATE discovery_versions SET status = 'finalized', size_bytes = ? WHERE id = ?")
    .bind(body.size_bytes ?? object.size, versionId)
    .run();
  await env.DB.prepare("UPDATE discoveries SET title = ?, updated_at = ?, latest_version_id = ? WHERE id = ?")
    .bind(version.title, now, versionId, discoveryId)
    .run();

  const tagIds = (
    await env.DB.prepare("SELECT tag_id FROM version_tags WHERE version_id = ?").bind(versionId).all<{ tag_id: number }>()
  ).results.map((row) => row.tag_id);
  await replaceDiscoveryTagsCache(env, discoveryId, tagIds);

  const url = `${new URL(request.url).origin}/d/${versionId}`;
  return Response.json({ id: versionId, url, version: version.version_number, title: version.title });
}

export async function listPublishedDiscoveries(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const creator = url.searchParams.get("creator");
  const type = url.searchParams.get("type");
  const tag = url.searchParams.get("tag");
  const q = url.searchParams.get("q");
  const sort = url.searchParams.get("sort") === "newest" ? "created_at" : "updated_at";

  const conditions: string[] = [];
  const bindings: unknown[] = [];
  if (creator) {
    conditions.push("d.created_by = ?");
    bindings.push(creator);
  }
  if (type) {
    conditions.push("d.type = ?");
    bindings.push(normalizeTag(type));
  }
  if (tag) {
    conditions.push(
      "d.id IN (SELECT dt.discovery_id FROM discovery_tags dt JOIN tags t ON t.id = dt.tag_id WHERE t.name = ?)"
    );
    bindings.push(normalizeTag(tag));
  }
  if (q) {
    conditions.push("d.title LIKE ? COLLATE NOCASE");
    bindings.push(`%${q}%`);
  }
  const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";

  const { results } = await env.DB.prepare(
    `SELECT d.id, d.slug, d.type, d.title, d.created_by, d.created_at, d.updated_at
     FROM discoveries d ${where} ORDER BY d.${sort} DESC LIMIT 200`
  )
    .bind(...bindings)
    .all<DiscoveryRow>();

  return Response.json({
    discoveries: results.map((row) => ({
      name: row.slug ?? row.id,
      type: row.type,
      title: row.title,
      created_by: row.created_by,
      updated_at: row.updated_at,
    })),
  });
}

export async function getPublishedDiscovery(env: Env, slugOrId: string): Promise<Response> {
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
    tags: tags.join(", "),
  });
}

export async function getPublishedVersions(env: Env, slugOrId: string): Promise<Response> {
  const discovery = await findDiscoveryBySlugOrId(env, slugOrId);
  if (!discovery) throw new ApiError(404, `no discovery ${slugOrId}`);

  const { results } = await env.DB.prepare(
    "SELECT version_number, title, uploaded_by, created_at FROM discovery_versions WHERE discovery_id = ? AND status = 'finalized' ORDER BY version_number DESC"
  )
    .bind(discovery.id)
    .all<{ version_number: number; title: string; uploaded_by: string; created_at: string }>();

  return Response.json({
    versions: results.map((row) => ({
      version: row.version_number,
      title: row.title,
      uploaded_by: row.uploaded_by,
      created_at: row.created_at,
    })),
  });
}
