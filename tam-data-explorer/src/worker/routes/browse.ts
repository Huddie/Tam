import { ApiError } from "../lib/errors";
import type { Env } from "../types";

export interface BrowseEntry {
  prefixes: string[];
  objects: Array<{ key: string; size: number; uploaded: string }>;
  cursor: string | null;
  total: number;
  pageSize: number;
}

export type Dataset = "minute" | "eod";

/** Validates a `dataset` query param into one of the two top-level R2
 * prefixes the Symbol/Year picker can browse -- "minute" (tam.marketdata's
 * 1-minute bars) or "eod" (tam.data's end-of-day bars, a DIFFERENT and
 * generally much broader symbol universe: raw indices like "^GSPC" and
 * decades of history for tickers the minute-bar feed never covers at all).
 * Defaults to "minute" when omitted, matching this API's behavior before
 * "eod" existed -- an existing caller that never sends `dataset` keeps
 * working unchanged. */
export function parseDataset(value: string | null): Dataset {
  if (value === null || value === "minute") return "minute";
  if (value === "eod") return "eod";
  throw new ApiError(400, `dataset must be "minute" or "eod", got ${JSON.stringify(value)}`);
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
 * still works correctly either way.
 *
 * Also returns `total`/`pageSize` -- the total item count at this exact
 * level (folders + files combined, same filtering as above) and the page
 * size used to produce it, so the UI can show "N items" and "page X of Y"
 * without a second request. `total` comes from folderItemCount() below,
 * which is its own full walk of every list() page under `prefix` (R2 has
 * no cheaper way to get a count) -- cached per-prefix, same reasoning as
 * bucketStats()'s cache further down this file. */
export async function browse(env: Env, prefix: string, cursor?: string): Promise<BrowseEntry> {
  const [page, total] = await Promise.all([
    env.DATA.list({ prefix, delimiter: "/", cursor, limit: BROWSE_PAGE_SIZE }),
    folderItemCount(env, prefix),
  ]);
  return {
    prefixes: page.delimitedPrefixes,
    objects: page.objects
      .filter((object) => !object.key.endsWith(".completeness.json"))
      .map((object) => ({ key: object.key, size: object.size, uploaded: object.uploaded.toISOString() })),
    cursor: page.truncated ? page.cursor : null,
    total,
    pageSize: BROWSE_PAGE_SIZE,
  };
}

const FOLDER_COUNT_CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes -- same tolerance as bucketStats() below
const FOLDER_COUNT_CACHE_MAX_ENTRIES = 200; // bounded FIFO -- isolate-local, not meant to hold every prefix ever browsed

// Isolate-local cache, same reasoning as bucketStats()'s own cache below --
// keyed per-prefix since "how many items total" needs its own walk of every
// R2 list() page under THIS prefix (delimiter-scoped, one level deep, same
// as what browse() itself shows) and R2 has no cheaper way to get a count.
const folderCountCache = new Map<string, { value: number; expiresAt: number }>();

async function folderItemCount(env: Env, prefix: string): Promise<number> {
  const now = Date.now();
  const cached = folderCountCache.get(prefix);
  if (cached && cached.expiresAt > now) return cached.value;

  let count = 0;
  let cursor: string | undefined;
  do {
    const page = await env.DATA.list({ prefix, delimiter: "/", cursor, limit: FETCH_BATCH_SIZE });
    count += page.delimitedPrefixes.length;
    count += page.objects.filter((object) => !object.key.endsWith(".completeness.json")).length;
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);

  if (folderCountCache.size >= FOLDER_COUNT_CACHE_MAX_ENTRIES) {
    const oldestKey = folderCountCache.keys().next().value;
    if (oldestKey !== undefined) folderCountCache.delete(oldestKey);
  }
  folderCountCache.set(prefix, { value: count, expiresAt: now + FOLDER_COUNT_CACHE_TTL_MS });
  return count;
}

/** GET /api/symbols?dataset=minute|eod -- every symbol under that dataset's
 * prefix, unpaginated: this is just a flat list of ticker names (thousands
 * of short strings at most, trivial payload size), so unlike browse()
 * above there's no reason to paginate it -- doing so would just push the
 * "which symbol am I missing" problem onto the symbol-picker UI instead of
 * solving it. Loops through every R2 list() page itself rather than
 * stopping at some cap; a previous version capped combined
 * prefixes+objects at 2000 and silently dropped anything past that, which
 * is exactly what caused symbols past roughly the first couple hundred
 * (alphabetically) to disappear once this bucket grew past that cap. */
export async function listSymbols(env: Env, dataset: Dataset): Promise<string[]> {
  const root = `${dataset}/`;
  const symbols: string[] = [];
  let cursor: string | undefined;
  do {
    const page = await env.DATA.list({ prefix: root, delimiter: "/", cursor, limit: FETCH_BATCH_SIZE });
    symbols.push(...page.delimitedPrefixes.map((prefix) => prefix.slice(root.length).replace(/\/$/, "")));
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  return symbols.sort();
}

/** GET /api/symbols/:symbol/years?dataset=minute|eod -- the yearly Parquet
 * files available for one symbol in that dataset (both tam/marketdata/
 * store.py's and tam/data/storage.py's own <root>/<SYMBOL>/<year>.parquet
 * layout, just under a different top-level prefix). A single symbol has at
 * most a few dozen yearly files, well under one R2 list() page, so no
 * pagination needed here either. */
export async function listYears(env: Env, symbol: string, dataset: Dataset): Promise<Array<{ year: number; key: string; size: number }>> {
  const { objects } = await browse(env, `${dataset}/${symbol.toUpperCase()}/`);
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

export interface DatasetStats {
  bytes: number;
  objects: number;
}

export interface BucketStats {
  totalBytes: number;
  totalObjects: number;
  byDataset: Record<Dataset | "other", DatasetStats>;
}

const STATS_CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes

// Isolate-local cache, not a real distributed cache (no KV/Durable Object
// binding for this) -- Workers can and do reuse the same isolate across
// several requests, so this cuts a full bucket scan down to roughly once
// per 5 minutes PER isolate rather than once per page load, without adding
// new infrastructure for what's ultimately a rough "how much are we
// using" display, not a billing-accurate figure. Worst case (a fresh
// isolate, or the TTL just expired) is a real bucket-wide list() scan --
// see bucketStats() below for why that's still bounded/acceptable.
let cachedStats: { value: BucketStats; expiresAt: number } | null = null;

/** GET /api/bucket-stats -- total bytes/object count across the whole
 * bucket, broken down by top-level prefix (minute/eod/other) so the
 * bucket's landing page can show "how much storage is this actually
 * using" at a glance. Walks EVERY object in the bucket (no delimiter) --
 * there's no cheaper way to get an exact byte total from R2's list API,
 * which is why this is cached (see cachedStats above) rather than
 * recomputed on every request. */
export async function bucketStats(env: Env): Promise<BucketStats> {
  const now = Date.now();
  if (cachedStats && cachedStats.expiresAt > now) return cachedStats.value;

  const byDataset: Record<Dataset | "other", DatasetStats> = {
    minute: { bytes: 0, objects: 0 },
    eod: { bytes: 0, objects: 0 },
    other: { bytes: 0, objects: 0 },
  };
  let totalBytes = 0;
  let totalObjects = 0;

  let cursor: string | undefined;
  do {
    const page = await env.DATA.list({ cursor, limit: FETCH_BATCH_SIZE });
    for (const object of page.objects) {
      totalBytes += object.size;
      totalObjects += 1;
      const bucket = object.key.startsWith("minute/") ? "minute" : object.key.startsWith("eod/") ? "eod" : "other";
      byDataset[bucket].bytes += object.size;
      byDataset[bucket].objects += 1;
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);

  const stats: BucketStats = { totalBytes, totalObjects, byDataset };
  cachedStats = { value: stats, expiresAt: now + STATS_CACHE_TTL_MS };
  return stats;
}
