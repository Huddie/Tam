// Single source of truth for tag AND type normalization -- the CLI/SDK never
// re-implement this; both just send raw strings and let the server collapse
// spelling variants ("After Hours" / "after-hours" / "after_hours" all land
// on the same normalized value).
export function normalizeTag(raw: string): string {
  return raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}
