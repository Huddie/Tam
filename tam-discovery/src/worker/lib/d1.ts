import type { DiscoveryRow, Env, ProjectRow, VersionRow } from "../types";
import { ApiError } from "./errors";
import { normalizeTag } from "./tags";

export function newId(): string {
  return crypto.randomUUID();
}

export function nowIso(): string {
  return new Date().toISOString();
}

export async function findDiscoveryBySlugOrId(env: Env, slugOrId: string): Promise<DiscoveryRow | null> {
  const row = await env.DB.prepare("SELECT * FROM discoveries WHERE id = ? OR slug = ?")
    .bind(slugOrId, slugOrId)
    .first<DiscoveryRow>();
  return row ?? null;
}

export async function findProjectBySlugOrId(env: Env, slugOrId: string): Promise<ProjectRow | null> {
  const row = await env.DB.prepare("SELECT * FROM projects WHERE id = ? OR slug = ?")
    .bind(slugOrId, slugOrId)
    .first<ProjectRow>();
  return row ?? null;
}

/** Resolves a `/d/:idOrSlug`-style path segment to (discovery, version): a
 * discovery's own id/slug always means "show the latest version", while a
 * VERSION's own id shows exactly that version, forever, regardless of what's
 * latest by the time it's visited later -- the immutability guarantee. */
export async function resolveVersionTarget(
  env: Env,
  idOrSlug: string
): Promise<{ discovery: DiscoveryRow; version: VersionRow } | null> {
  const discovery = await findDiscoveryBySlugOrId(env, idOrSlug);
  if (discovery) {
    const version = await env.DB.prepare("SELECT * FROM discovery_versions WHERE id = ?")
      .bind(discovery.latest_version_id)
      .first<VersionRow>();
    return version ? { discovery, version } : null;
  }

  const version = await env.DB.prepare("SELECT * FROM discovery_versions WHERE id = ? AND status = 'finalized'")
    .bind(idOrSlug)
    .first<VersionRow>();
  if (!version) return null;

  const owningDiscovery = await env.DB.prepare("SELECT * FROM discoveries WHERE id = ?")
    .bind(version.discovery_id)
    .first<DiscoveryRow>();
  return owningDiscovery ? { discovery: owningDiscovery, version } : null;
}

/** Normalizes + dedupes `rawTags`, inserting any brand-new tag names, and
 * returns their ids. Rejects (400) a tag that's empty after normalization --
 * silently dropping it would let e.g. a lone "--" tag disappear without the
 * publisher ever knowing it didn't take. */
export async function upsertTagIds(env: Env, rawTags: string[]): Promise<number[]> {
  const seen = new Set<string>();
  const ids: number[] = [];

  for (const raw of rawTags) {
    const name = normalizeTag(raw);
    if (!name) throw new ApiError(400, `tag ${JSON.stringify(raw)} is empty after normalization`);
    if (seen.has(name)) continue;
    seen.add(name);

    await env.DB.prepare("INSERT INTO tags (name) VALUES (?) ON CONFLICT(name) DO NOTHING").bind(name).run();
    const row = await env.DB.prepare("SELECT id FROM tags WHERE name = ?").bind(name).first<{ id: number }>();
    if (row) ids.push(row.id);
  }
  return ids;
}

export async function linkVersionTags(env: Env, versionId: string, tagIds: number[]): Promise<void> {
  for (const tagId of tagIds) {
    await env.DB.prepare("INSERT INTO version_tags (version_id, tag_id) VALUES (?, ?)").bind(versionId, tagId).run();
  }
}

/** discovery_tags is a fast-lookup CACHE of a discovery's current tags
 * (derived from its latest version) -- rebuilt wholesale on every finalize,
 * never incrementally patched, so it can never drift from "whatever the
 * latest finalized version's own version_tags say". */
export async function replaceDiscoveryTagsCache(env: Env, discoveryId: string, tagIds: number[]): Promise<void> {
  await env.DB.prepare("DELETE FROM discovery_tags WHERE discovery_id = ?").bind(discoveryId).run();
  for (const tagId of tagIds) {
    await env.DB.prepare("INSERT INTO discovery_tags (discovery_id, tag_id) VALUES (?, ?)").bind(discoveryId, tagId).run();
  }
}

export async function tagNamesForDiscovery(env: Env, discoveryId: string): Promise<string[]> {
  const { results } = await env.DB.prepare(
    "SELECT t.name FROM tags t JOIN discovery_tags dt ON dt.tag_id = t.id WHERE dt.discovery_id = ? ORDER BY t.name"
  )
    .bind(discoveryId)
    .all<{ name: string }>();
  return results.map((row) => row.name);
}

export async function tagNamesForVersion(env: Env, versionId: string): Promise<string[]> {
  const { results } = await env.DB.prepare(
    "SELECT t.name FROM tags t JOIN version_tags vt ON vt.tag_id = t.id WHERE vt.version_id = ? ORDER BY t.name"
  )
    .bind(versionId)
    .all<{ name: string }>();
  return results.map((row) => row.name);
}
