import type { Env } from "../types";

export async function listTags(env: Env): Promise<Response> {
  const { results } = await env.DB.prepare(
    "SELECT DISTINCT t.name FROM tags t JOIN discovery_tags dt ON dt.tag_id = t.id ORDER BY t.name"
  ).all<{ name: string }>();
  return Response.json({ tags: results.map((row) => row.name) });
}

export async function listTypes(env: Env): Promise<Response> {
  const { results } = await env.DB.prepare("SELECT DISTINCT type FROM discoveries ORDER BY type").all<{ type: string }>();
  return Response.json({ types: results.map((row) => row.type) });
}
