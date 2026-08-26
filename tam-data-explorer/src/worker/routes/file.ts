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
 * nothing about the browse UI assumes that stays true forever). */
export async function downloadRaw(env: Env, key: string): Promise<Response> {
  const object = await fetchObject(env, key);
  const filename = key.split("/").pop() ?? key;
  return new Response(object.body, {
    headers: {
      "Content-Type": object.httpMetadata?.contentType ?? "application/octet-stream",
      "Content-Disposition": `attachment; filename="${filename}"`,
    },
  });
}
