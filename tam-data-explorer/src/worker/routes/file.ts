import { arrayBufferSource, r2AsyncBuffer, readParquetAll, readParquetDateIndex, readParquetFiltered, readParquetPage } from "../lib/parquet";
import type { AsyncBuffer } from "hyparquet";
import { rowsToCsv } from "../lib/csv";
import { ApiError } from "../lib/errors";
import type { Env } from "../types";

const DEFAULT_PAGE_SIZE = 50;
const MAX_PAGE_SIZE = 1000;

const YEAR_KEY_RE = /\/(\d{4})\.parquet$/;

/** `minute/<SYMBOL>/<year>.parquet` is the only layout tam.marketdata ever
 * writes (see store.py) -- the year is already encoded in the key itself,
 * so a month/day filter needs no extra request parameter for it. */
function parseYearFromKey(key: string): number {
  const match = YEAR_KEY_RE.exec(key);
  if (!match) throw new ApiError(400, `${key} doesn't look like a <SYMBOL>/<year>.parquet key`);
  return Number(match[1]);
}

async function fetchObject(env: Env, key: string) {
  const object = await env.DATA.get(key);
  if (!object) throw new ApiError(404, `no object at key ${key}`);
  return object;
}

/** Builds a lazy, range-request-backed AsyncBuffer for `key` instead of
 * downloading the whole R2 object -- head() gets the size with no body
 * transfer, then each byte range hyparquet actually asks for (the footer,
 * then just the row groups a page/filtered read touches) becomes its own
 * R2 range-GET. See lib/parquet.ts's r2AsyncBuffer for why small files
 * still cost exactly one request either way. */
async function r2ParquetFile(env: Env, key: string): Promise<AsyncBuffer> {
  const head = await env.DATA.head(key);
  if (!head) throw new ApiError(404, `no object at key ${key}`);
  return r2AsyncBuffer(head.size, async (offset, length) => {
    const object = await env.DATA.get(key, { range: { offset, length } });
    if (!object) throw new Error(`no object at key ${key} (range ${offset}+${length})`);
    return object.arrayBuffer();
  });
}

/** Resolves whichever row-range the caller actually asked for -- an
 * explicit `start`/`end` date pair (a custom range picked in the UI) takes
 * priority over `month`/`day` (the month/day browse tree), and neither
 * being present means "no filter, page the whole file." Kept as one place
 * so viewFile() itself doesn't need to know there are two different ways
 * to ask for a range. */
function resolveRange(
  key: string,
  month?: number,
  day?: number,
  start?: string,
  end?: string,
): { start: Date; end: Date } | null {
  if (start) {
    const rangeStart = new Date(`${start}T00:00:00Z`);
    if (Number.isNaN(rangeStart.getTime())) throw new ApiError(400, `start=${start} is not a valid date`);
    const endBasis = end ? new Date(`${end}T00:00:00Z`) : rangeStart;
    if (Number.isNaN(endBasis.getTime())) throw new ApiError(400, `end=${end} is not a valid date`);
    // end is the LAST included day -- the actual upper bound passed to the
    // $lt filter is the day after it, same "day + 1" exclusive-upper-bound
    // convention the month/day branch below already uses.
    const rangeEnd = new Date(endBasis.getTime() + 24 * 60 * 60 * 1000);
    return { start: rangeStart, end: rangeEnd };
  }
  if (month) {
    const year = parseYearFromKey(key);
    return {
      start: new Date(Date.UTC(year, month - 1, day ?? 1)),
      end: day ? new Date(Date.UTC(year, month - 1, day + 1)) : new Date(Date.UTC(year, month, 1)),
    };
  }
  return null;
}

/** GET /api/file?key=&page=&pageSize=[&month=&day=][&start=&end=] -- a
 * paginated view of a Parquet file's rows, optionally scoped to one UTC
 * month (`month` only), one UTC day (`month`+`day`), or an arbitrary
 * inclusive UTC date range (`start`[+`end`], `YYYY-MM-DD`) -- see
 * resolveRange() above for exactly how these combine. Non-.parquet keys
 * are rejected (400): this route is specifically the tabular viewer, not a
 * general-purpose file fetcher -- see downloadRaw() below for that. */
export async function viewFile(
  env: Env,
  key: string,
  page: number,
  pageSize: number,
  month?: number,
  day?: number,
  start?: string,
  end?: string,
): Promise<Response> {
  if (!key.endsWith(".parquet")) throw new ApiError(400, `${key} is not a .parquet file`);
  const file = await r2ParquetFile(env, key);

  const boundedPageSize = Math.min(Math.max(1, pageSize || DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE);
  const boundedPage = Math.max(1, page || 1);
  const range = resolveRange(key, month, day, start, end);

  const result = range
    ? await readParquetFiltered(file, range, boundedPage, boundedPageSize)
    : await readParquetPage(file, boundedPage, boundedPageSize);

  return Response.json({ ...result, page: boundedPage, pageSize: boundedPageSize });
}

/** GET /api/file/dates?key= -- which UTC months/days actually have data in
 * this year's file, for the "browse by month/day" folder UI (see
 * lib/parquet.ts's readParquetDateIndex for why this needs no
 * day-partitioned storage change at all). */
export async function viewFileDates(env: Env, key: string): Promise<Response> {
  if (!key.endsWith(".parquet")) throw new ApiError(400, `${key} is not a .parquet file`);
  const file = await r2ParquetFile(env, key);
  return Response.json(await readParquetDateIndex(file));
}

/** GET /api/file/completeness?key= -- the actual/expected-minutes index
 * tam.marketdata.completeness computed and wrote at ingest time, next to
 * this same year's parquet file (see MinuteBarStore._upsert_partition on
 * the Python side) -- this route just reads that JSON sidecar back
 * verbatim. Never recomputed here: porting a full NYSE trading calendar
 * into a Cloudflare Worker isn't worth it for something already computed
 * correctly in Python at write time. 404s (not a 200 with nulls) for a
 * file ingested before this feature existed, or ingested without the
 * `marketdata` extra's pandas_market_calendars installed -- the frontend
 * just omits the completeness badge in that case rather than showing
 * misleading zeros. */
export async function viewFileCompleteness(env: Env, key: string): Promise<Response> {
  if (!key.endsWith(".parquet")) throw new ApiError(400, `${key} is not a .parquet file`);
  const sidecarKey = key.replace(/\.parquet$/, ".completeness.json");
  const object = await env.DATA.get(sidecarKey);
  if (!object) throw new ApiError(404, `no completeness index at ${sidecarKey}`);
  return new Response(object.body, { headers: { "Content-Type": "application/json" } });
}

/** GET /api/file/csv?key= -- every row (not just the current page), as a
 * downloadable CSV -- deliberately unpaginated so exporting isn't
 * artificially truncated to the table view's own page size. */
export async function downloadCsv(env: Env, key: string): Promise<Response> {
  if (!key.endsWith(".parquet")) throw new ApiError(400, `${key} is not a .parquet file`);
  const object = await fetchObject(env, key);
  const buffer = await object.arrayBuffer();
  const { columns, rows } = await readParquetAll(arrayBufferSource(buffer));
  const csv = rowsToCsv(columns, rows);

  const filename = key.split("/").pop()!.replace(/\.parquet$/, ".csv");
  return new Response(csv, {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="${filename}"`,
    },
  });
}

/** GET /api/download?key= -- a plain passthrough for whatever's at `key`,
 * for browsing non-Parquet objects that the paginated viewer can't render
 * (this bucket is Parquet-only today per tam.marketdata's own design, but
 * nothing about the browse UI assumes that stays true forever).
 *
 * Also the endpoint DuckDB's httpfs/read_parquet() hits directly for
 * token-authenticated querying (see routes/token-api.ts) -- httpfs reads
 * Parquet footers/row-groups via HTTP range requests rather than
 * downloading the whole file, so Range support here isn't optional for
 * that use case. */
export async function downloadRaw(env: Env, key: string, rangeHeader: string | null): Promise<Response> {
  const range = parseRange(rangeHeader);
  const object = range ? await env.DATA.get(key, { range }) : await env.DATA.get(key);
  if (!object) throw new ApiError(404, `no object at key ${key}`);

  const filename = key.split("/").pop() ?? key;
  const headers: Record<string, string> = {
    "Content-Type": object.httpMetadata?.contentType ?? "application/octet-stream",
    "Content-Disposition": `attachment; filename="${filename}"`,
    "Accept-Ranges": "bytes",
  };

  // R2Object's own `range` field (only set when the .get() call above used
  // one) tells us exactly which bytes came back, needed for a correct
  // Content-Range header -- R2 clamps an out-of-bounds range to what's
  // actually available rather than erroring, so this reflects reality
  // rather than echoing the request back unchecked.
  const objectWithRange = object as R2ObjectBody & { range?: { offset: number; length: number } };
  if (objectWithRange.range) {
    const { offset, length } = objectWithRange.range;
    headers["Content-Range"] = `bytes ${offset}-${offset + length - 1}/${object.size}`;
    headers["Content-Length"] = String(length);
    return new Response(object.body, { status: 206, headers });
  }

  headers["Content-Length"] = String(object.size);
  return new Response(object.body, { headers });
}

/** Parses a standard single-range `Range: bytes=start-end` header into R2's
 * own `{offset, length}` shape. Suffix ranges (`bytes=-500`, "last 500
 * bytes") map to R2's `{suffix}` form; anything malformed or absent is
 * treated as "no range requested" (a full-object GET) rather than a 4xx --
 * matches how most HTTP servers degrade when a client sends a Range header
 * they don't have to honor. */
function parseRange(header: string | null): R2Range | undefined {
  if (!header) return undefined;
  const match = /^bytes=(\d*)-(\d*)$/.exec(header.trim());
  if (!match) return undefined;
  const [, startStr, endStr] = match;

  if (!startStr && endStr) return { suffix: Number(endStr) };
  if (!startStr) return undefined;

  const offset = Number(startStr);
  if (!endStr) return { offset };
  const end = Number(endStr);
  return { offset, length: end - offset + 1 };
}
