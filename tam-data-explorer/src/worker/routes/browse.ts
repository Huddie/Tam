import type { Env } from "../types";

export interface BrowseEntry {
  prefixes: string[];
  objects: Array<{ key: string; size: number; uploaded: string }>;
  truncated: boolean;
}

const MAX_KEYS = 2000;

/** GET /api/browse?prefix= -- one "directory listing" level of the bucket,
 * using R2's delimiter support so this doesn't have to fetch every key
 * under a prefix just to show its immediate children. Caps out at
 * MAX_KEYS combined prefixes+objects (an internal-tool safety valve, not a
 * real pagination UI -- this bucket's actual shape is a few hundred
 * symbols with a handful of yearly files each, nowhere near this). */
export async function browse(env: Env, prefix: string): Promise<BrowseEntry> {
  const prefixes: string[] = [];
  const objects: BrowseEntry["objects"] = [];
  let cursor: string | undefined;
  let truncated = false;

  do {
    const page = await env.DATA.list({ prefix, delimiter: "/", cursor, limit: 1000 });
    prefixes.push(...page.delimitedPrefixes);
    objects.push(...page.objects.map((object) => ({ key: object.key, size: object.size, uploaded: object.uploaded.toISOString() })));
    cursor = page.truncated ? page.cursor : undefined;
    if (prefixes.length + objects.length >= MAX_KEYS) {
      truncated = Boolean(cursor);
      break;
    }
  } while (cursor);

  return { prefixes, objects, truncated };
}

/** GET /api/symbols -- convenience wrapper around browse("minute/") for the
 * symbol/year picker UI, so it doesn't need to know the "minute/" prefix or
 * strip trailing slashes itself. */
export async function listSymbols(env: Env): Promise<string[]> {
  const { prefixes } = await browse(env, "minute/");
  return prefixes.map((prefix) => prefix.replace(/^minute\//, "").replace(/\/$/, "")).sort();
}

/** GET /api/symbols/:symbol/years -- the yearly Parquet files available for
 * one symbol (tam/marketdata/store.py's own <root>/<SYMBOL>/<year>.parquet
 * layout), each with its object key ready to hand straight to the file
 * view route. */
export async function listYears(env: Env, symbol: string): Promise<Array<{ year: number; key: string; size: number }>> {
  const { objects } = await browse(env, `minute/${symbol.toUpperCase()}/`);
  return objects
    .filter((object) => object.key.endsWith(".parquet"))
    .map((object) => ({
      year: Number(object.key.split("/").pop()!.replace(".parquet", "")),
      key: object.key,
      size: object.size,
    }))
    .sort((a, b) => b.year - a.year);
}

/** Every object key under `prefix`, at ANY depth (no delimiter) -- used by
 * the export route to resolve a folder-level export into its concrete list
 * of .parquet files. Capped at MAX_KEYS for the same reason browse() is. */
export async function listAllKeysUnderPrefix(env: Env, prefix: string): Promise<Array<{ key: string; size: number }>> {
  const keys: Array<{ key: string; size: number }> = [];
  let cursor: string | undefined;

  do {
    const page = await env.DATA.list({ prefix, cursor, limit: 1000 });
    keys.push(...page.objects.map((object) => ({ key: object.key, size: object.size })));
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor && keys.length < MAX_KEYS);

  return keys;
}
