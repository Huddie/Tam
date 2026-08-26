import type { Env } from "../types";

export interface BrowseEntry {
  prefixes: string[];
  objects: Array<{ key: string; size: number; uploaded: string }>;
  cursor: string | null;
}

const BROWSE_PAGE_SIZE = 50; // one browse() page shown to the user at a time
const FETCH_BATCH_SIZE = 1000; // R2's own per-call max -- only used by the two loops below that aggregate ALL pages internally, unrelated to what the user sees per page

/** GET /api/browse?prefix=&cursor= -- one page (>=1 R2 list() call's worth)
 * of a "directory listing" level of the bucket, using R2's delimiter
 * support so this doesn't have to fetch every key under a prefix just to
 * show its immediate children. Returns a `cursor` for the next page when
 * there's more -- real pagination, not a silent truncation, since this
 * bucket now holds far more than "a few hundred symbols" (the assumption
 * an earlier MAX_KEYS-capped version of this function made, which quietly
 * cut off anything past the cap).
 *
 * Completeness sidecars (<year>.completeness.json, written next to each
 * <year>.parquet by tam.marketdata.completeness) are filtered out here,
 * unconditionally -- they're pure metadata, never independently openable
 * in the table view, so there's no "show hidden" toggle that should ever
 * reveal them. Underscore-prefixed folders/files (e.g. _diag/, _test/,
 * the ingestion manifest _manifest.json) are NOT filtered here on
 * purpose -- those ARE real, occasionally-useful things to browse, so
 * whether to show them is a client-side "Show hidden" toggle (see
 * BrowsePage.tsx) rather than a server-side always-off decision.
 * Filtered post-fetch (R2's own .list() has no "exclude this" option), so
 * an individual page can occasionally come back with fewer than
 * BROWSE_PAGE_SIZE visible entries -- harmless, cursor-based pagination
 * still works correctly either way. */
export async function browse(env: Env, prefix: string, cursor?: string): Promise<BrowseEntry> {
  const page = await env.DATA.list({ prefix, delimiter: "/", cursor, limit: BROWSE_PAGE_SIZE });
  return {
    prefixes: page.delimitedPrefixes,
    objects: page.objects
      .filter((object) => !object.key.endsWith(".completeness.json"))
      .map((object) => ({ key: object.key, size: object.size, uploaded: object.uploaded.toISOString() })),
    cursor: page.truncated ? page.cursor : null,
  };
}

/** GET /api/symbols -- every symbol under "minute/", unpaginated: this is
 * just a flat list of ticker names (thousands of short strings at most,
 * trivial payload size), so unlike browse() above there's no reason to
 * paginate it -- doing so would just push the "which symbol am I missing"
 * problem onto the symbol-picker UI instead of solving it. Loops through
 * every R2 list() page itself rather than stopping at some cap; a
 * previous version capped combined prefixes+objects at 2000 and silently
 * dropped anything past that, which is exactly what caused symbols past
 * roughly the first couple hundred (alphabetically) to disappear once this
 * bucket grew past that cap. */
export async function listSymbols(env: Env): Promise<string[]> {
  const symbols: string[] = [];
  let cursor: string | undefined;
  do {
    const page = await env.DATA.list({ prefix: "minute/", delimiter: "/", cursor, limit: FETCH_BATCH_SIZE });
    symbols.push(...page.delimitedPrefixes.map((prefix) => prefix.replace(/^minute\//, "").replace(/\/$/, "")));
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  return symbols.sort();
}

/** GET /api/symbols/:symbol/years -- the yearly Parquet files available for
 * one symbol (tam/marketdata/store.py's own <root>/<SYMBOL>/<year>.parquet
 * layout), each with its object key ready to hand straight to the file
 * view route. A single symbol has at most a few dozen yearly files, well
 * under one R2 list() page, so no pagination needed here either. */
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
 * of .parquet files. Capped at MAX_EXPORT_KEYS as a memory safety valve
 * for the Worker itself (a single export request materializing an
 * unbounded key list) -- unlike browse()/listSymbols() above, silent
 * truncation here is an acceptable tradeoff since it only affects the
 * (rare) case of exporting an enormous folder in one request, not
 * everyday browsing/listing. */
const MAX_EXPORT_KEYS = 20000;
export async function listAllKeysUnderPrefix(env: Env, prefix: string): Promise<Array<{ key: string; size: number }>> {
  const keys: Array<{ key: string; size: number }> = [];
  let cursor: string | undefined;

  do {
    const page = await env.DATA.list({ prefix, cursor, limit: FETCH_BATCH_SIZE });
    keys.push(...page.objects.map((object) => ({ key: object.key, size: object.size })));
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor && keys.length < MAX_EXPORT_KEYS);

  return keys;
}
