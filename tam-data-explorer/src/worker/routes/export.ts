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

/** Resolves any combination of `prefixes` (each expanded recursively, e.g.
 * from a multi-folder selection) and explicit `keys` into one deduplicated
 * key list -- a folder and an individually-picked file under a DIFFERENT
 * folder can be selected together in the same export. */
async function resolveExportKeys(env: Env, prefixes: string[], keys: string[]): Promise<string[]> {
  const collected = new Set(keys);

  if (prefixes.length) {
    let totalFiles = 0;
    let totalBytes = 0;
    for (const prefix of prefixes) {
      const found = (await listAllKeysUnderPrefix(env, prefix)).filter((entry) => entry.key.endsWith(".parquet"));
      totalFiles += found.length;
      totalBytes += found.reduce((sum, entry) => sum + entry.size, 0);
      found.forEach((entry) => collected.add(entry.key));
    }
    if (totalFiles > MAX_EXPORT_FILES || totalBytes > MAX_EXPORT_BYTES) {
      throw new ApiError(
        400,
        `${totalFiles} files (${totalBytes} bytes) under the selected folder(s) is too much to export at once -- select fewer`
      );
    }
  }

  if (!collected.size) throw new ApiError(400, "prefix or at least one key is required");
  return [...collected];
}

function exportBaseName(prefixes: string[], keys: string[]): string {
  const raw =
    prefixes.length === 1 && !keys.length
      ? prefixes[0].replace(/\/$/, "").split("/").pop()
      : prefixes.length + keys.length === 1
        ? keys[0]?.split("/").pop()?.replace(/\.parquet$/, "")
        : "export";
  return (raw || "export").replace(/[^a-zA-Z0-9_-]/g, "_");
}

/** GET /api/export?format=parquet|csv&prefix=...&prefix=... (one or more
 * folders, each expanded recursively) and/or &key=...&key=... (specific
 * files) -- any mix of both. "parquet" zips up the original files
 * unmodified; "csv" reads and concatenates every file's rows into one
 * combined CSV -- correct here because every minute-bar file already
 * carries its own `symbol` column (tam/marketdata/schema.py), so
 * concatenating rows across symbols/years/folders is meaningful, not just
 * a byte dump. */
export async function exportFiles(env: Env, prefixes: string[], keys: string[], format: "parquet" | "csv"): Promise<Response> {
  const resolvedKeys = await resolveExportKeys(env, prefixes, keys);
  if (!resolvedKeys.length) throw new ApiError(404, "no .parquet files found to export");
  const baseName = exportBaseName(prefixes, keys);

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
