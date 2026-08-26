import { zipSync } from "fflate";
import { readParquetAll } from "../lib/parquet";
import { rowsToCsv } from "../lib/csv";
import { ApiError } from "../lib/errors";
import { listAllKeysUnderPrefix } from "./browse";
import type { Env } from "../types";

// Workers get a ~128MB heap -- zipping needs roughly 2x the source bytes in
// memory at once (raw bytes + the deflated copy being built), and CSV
// concatenation decodes every file's rows into JS objects simultaneously
// (typically several times larger than the compressed Parquet bytes). This
// cap is deliberately conservative against the SOURCE (compressed) size as
// a proxy for both cases, not a precise memory model.
const MAX_EXPORT_FILES = 200;
const MAX_EXPORT_BYTES = 50 * 1024 * 1024;

async function resolveExportKeys(env: Env, prefix: string | null, keys: string[]): Promise<string[]> {
  if (keys.length) return keys;
  if (!prefix) throw new ApiError(400, "prefix or at least one key is required");

  const found = (await listAllKeysUnderPrefix(env, prefix)).filter((entry) => entry.key.endsWith(".parquet"));
  const totalBytes = found.reduce((sum, entry) => sum + entry.size, 0);
  if (found.length > MAX_EXPORT_FILES || totalBytes > MAX_EXPORT_BYTES) {
    throw new ApiError(
      400,
      `${found.length} files (${totalBytes} bytes) under ${prefix} is too much to export at once -- narrow the folder`
    );
  }
  return found.map((entry) => entry.key);
}

function exportBaseName(prefix: string | null, keys: string[]): string {
  const raw =
    prefix?.replace(/\/$/, "").split("/").pop() ?? (keys.length === 1 ? keys[0].split("/").pop()?.replace(/\.parquet$/, "") : "export");
  return (raw || "export").replace(/[^a-zA-Z0-9_-]/g, "_");
}

/** GET /api/export?format=parquet|csv&prefix=... (a whole folder,
 * recursively) or &key=...&key=... (specific files). "parquet" zips up the
 * original files unmodified; "csv" reads and concatenates every file's rows
 * into one combined CSV -- correct here because every minute-bar file
 * already carries its own `symbol` column (tam/marketdata/schema.py), so
 * concatenating rows across symbols/years is meaningful, not just a byte
 * dump. */
export async function exportFiles(env: Env, prefix: string | null, keys: string[], format: "parquet" | "csv"): Promise<Response> {
  const resolvedKeys = await resolveExportKeys(env, prefix, keys);
  if (!resolvedKeys.length) throw new ApiError(404, "no .parquet files found to export");
  const baseName = exportBaseName(prefix, resolvedKeys);

  if (format === "parquet") {
    const entries: Record<string, Uint8Array> = {};
    for (const key of resolvedKeys) {
      const object = await env.DATA.get(key);
      if (object) entries[key] = new Uint8Array(await object.arrayBuffer());
    }
    return new Response(zipSync(entries), {
      headers: {
        "Content-Type": "application/zip",
        "Content-Disposition": `attachment; filename="${baseName}.zip"`,
      },
    });
  }

  let columns: string[] = [];
  const rows: Record<string, unknown>[] = [];
  for (const key of resolvedKeys) {
    const object = await env.DATA.get(key);
    if (!object) continue;
    const result = await readParquetAll(await object.arrayBuffer());
    if (!columns.length) columns = result.columns;
    rows.push(...result.rows);
  }
  return new Response(rowsToCsv(columns, rows), {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="${baseName}.csv"`,
    },
  });
}
