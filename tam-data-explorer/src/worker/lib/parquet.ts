import { parquetMetadataAsync, parquetReadObjects } from "hyparquet";
import type { AsyncBuffer } from "hyparquet";

export interface ParquetPage {
  columns: string[];
  rows: Record<string, unknown>[];
  totalRows: number;
}

/** Wraps an already-fully-fetched ArrayBuffer as the AsyncBuffer interface
 * hyparquet expects. The minute-bar files this Worker reads are small
 * (~98k rows/symbol/year per tam/marketdata/store.py's own docstring, so a
 * few MB compressed at most) -- simple enough to hold the whole object in
 * memory rather than implementing real byte-range fetching against R2. */
function bufferSource(buffer: ArrayBuffer): AsyncBuffer {
  return {
    byteLength: buffer.byteLength,
    async slice(start: number, end?: number) {
      return buffer.slice(start, end ?? buffer.byteLength);
    },
  };
}

/** JSON.stringify (and therefore Response.json()) throws on bigint --
 * Parquet INT64 columns (e.g. volume) decode to JS bigint in hyparquet.
 * Minute-bar volumes are always far below Number.MAX_SAFE_INTEGER, so a
 * plain Number conversion is safe here. Dates pass through untouched --
 * JSON.stringify already calls Date.prototype.toJSON() for those. */
function sanitizeForJson(rows: Record<string, unknown>[]): Record<string, unknown>[] {
  return rows.map((row) => {
    const clean: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(row)) {
      clean[key] = typeof value === "bigint" ? Number(value) : value;
    }
    return clean;
  });
}

/** One page of rows (rowStart/rowEnd, 0-indexed half-open range) from a
 * Parquet file already fully read into memory, plus the total row count so
 * the caller can render pagination controls. */
export async function readParquetPage(buffer: ArrayBuffer, page: number, pageSize: number): Promise<ParquetPage> {
  const file = bufferSource(buffer);
  const metadata = await parquetMetadataAsync(file);
  const totalRows = Number(metadata.num_rows);

  const rowStart = Math.max(0, (page - 1) * pageSize);
  const rowEnd = Math.min(totalRows, rowStart + pageSize);

  const rows = await parquetReadObjects({ file, metadata, rowStart, rowEnd });
  const columns = rows.length ? Object.keys(rows[0]) : [];

  return { columns, rows: sanitizeForJson(rows), totalRows };
}

/** Every row, unpaginated -- used for "download as CSV" so the export isn't
 * artificially truncated to whatever page size the table view happens to
 * be showing. */
export async function readParquetAll(buffer: ArrayBuffer): Promise<{ columns: string[]; rows: Record<string, unknown>[] }> {
  const file = bufferSource(buffer);
  const rows = await parquetReadObjects({ file });
  const columns = rows.length ? Object.keys(rows[0]) : [];
  return { columns, rows: sanitizeForJson(rows) };
}

export interface DateIndex {
  months: { month: number; days: number[] }[];
}

function toDate(value: unknown): Date {
  return value instanceof Date ? value : new Date(value as string | number);
}

/** Which UTC months/days actually have data in this year's file -- built by
 * decoding just the `ts` column (a cheap projection, not the full row) and
 * grouping distinct UTC month/day values out of it. This drives a "browse
 * by month/day" folder UI as a pure QUERY-TIME view over the same single
 * year-file tam.marketdata already writes (minute/<SYMBOL>/<year>.parquet)
 * -- no day-partitioned storage needed, and only months/days that genuinely
 * have rows show up (not a blind 1-12/1-31 grid). */
export async function readParquetDateIndex(buffer: ArrayBuffer): Promise<DateIndex> {
  const file = bufferSource(buffer);
  const rows = await parquetReadObjects({ file, columns: ["ts"] });

  const byMonth = new Map<number, Set<number>>();
  for (const row of rows) {
    const ts = toDate(row.ts);
    const month = ts.getUTCMonth() + 1;
    const day = ts.getUTCDate();
    if (!byMonth.has(month)) byMonth.set(month, new Set());
    byMonth.get(month)!.add(day);
  }

  const months = [...byMonth.entries()]
    .sort(([a], [b]) => a - b)
    .map(([month, days]) => ({ month, days: [...days].sort((a, b) => a - b) }));
  return { months };
}

/** Same page shape as readParquetPage(), scoped to a single UTC month
 * (`day` omitted) or a single UTC day (`day` given) within `year` -- via
 * hyparquet's own $gte/$lt range filter directly on `ts` (the same
 * tz-aware UTC timestamp tam.marketdata writes; no separate month/day
 * columns needed in the file itself), then paginated in memory. One
 * day/month's rows are a small fraction of a whole year, so decoding the
 * filtered set before slicing is cheap -- same reasoning readParquetAll()
 * already relies on for CSV export. */
export async function readParquetFiltered(
  buffer: ArrayBuffer,
  range: { year: number; month: number; day?: number },
  page: number,
  pageSize: number,
): Promise<ParquetPage> {
  const { year, month, day } = range;
  const start = new Date(Date.UTC(year, month - 1, day ?? 1));
  const end = day ? new Date(Date.UTC(year, month - 1, day + 1)) : new Date(Date.UTC(year, month, 1));

  const file = bufferSource(buffer);
  const rows = await parquetReadObjects({ file, filter: { ts: { $gte: start, $lt: end } } });

  const totalRows = rows.length;
  const rowStart = Math.max(0, (page - 1) * pageSize);
  const rowEnd = Math.min(totalRows, rowStart + pageSize);
  const pageRows = rows.slice(rowStart, rowEnd);
  const columns = pageRows.length ? Object.keys(pageRows[0]) : rows.length ? Object.keys(rows[0]) : [];

  return { columns, rows: sanitizeForJson(pageRows), totalRows };
}
