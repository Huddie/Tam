import { readParquetAll, readParquetPage } from "../lib/parquet";
import { rowsToCsv } from "../lib/csv";
import { ApiError } from "../lib/errors";
import type { Env } from "../types";

const DEFAULT_PAGE_SIZE = 200;
const MAX_PAGE_SIZE = 1000;

async function fetchObject(env: Env, key: string) {
  const object = await env.DATA.get(key);
  if (!object) throw new ApiError(404, `no object at key ${key}`);
  return object;
}

/** GET /api/file?key=&page=&pageSize= -- a paginated view of a Parquet
 * file's rows. Non-.parquet keys are rejected (400): this route is
 * specifically the tabular viewer, not a general-purpose file fetcher --
 * see downloadRaw() below for that. */
export async function viewFile(env: Env, key: string, page: number, pageSize: number): Promise<Response> {
  if (!key.endsWith(".parquet")) throw new ApiError(400, `${key} is not a .parquet file`);
  const object = await fetchObject(env, key);
  const buffer = await object.arrayBuffer();

  const boundedPageSize = Math.min(Math.max(1, pageSize || DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE);
  const result = await readParquetPage(buffer, Math.max(1, page || 1), boundedPageSize);

  return Response.json({ ...result, page: Math.max(1, page || 1), pageSize: boundedPageSize });
}

/** GET /api/file/csv?key= -- every row (not just the current page), as a
 * downloadable CSV -- deliberately unpaginated so exporting isn't
 * artificially truncated to the table view's own page size. */
export async function downloadCsv(env: Env, key: string): Promise<Response> {
  if (!key.endsWith(".parquet")) throw new ApiError(400, `${key} is not a .parquet file`);
  const object = await fetchObject(env, key);
  const buffer = await object.arrayBuffer();
  const { columns, rows } = await readParquetAll(buffer);
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
