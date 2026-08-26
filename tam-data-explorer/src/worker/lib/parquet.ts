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
