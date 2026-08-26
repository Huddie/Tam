export interface BrowseResult {
  prefixes: string[];
  objects: Array<{ key: string; size: number; uploaded: string }>;
  cursor: string | null;
}

export interface FilePage {
  columns: string[];
  rows: Record<string, unknown>[];
  totalRows: number;
  page: number;
  pageSize: number;
}

export interface YearEntry {
  year: number;
  key: string;
  size: number;
}

export interface DateIndex {
  months: { month: number; days: number[] }[];
}

export interface DayCompleteness {
  day: number;
  actual_bars: number;
  expected_bars: number;
  extended_hours_bars: number;
}

export interface MonthCompleteness {
  month: number;
  actual_bars: number;
  expected_bars: number;
  extended_hours_bars: number;
  days: DayCompleteness[];
}

export interface CompletenessIndex {
  symbol: string;
  year: number;
  calendar: string;
  actual_bars: number;
  expected_bars: number;
  extended_hours_bars: number;
  months: MonthCompleteness[];
}

export interface TokenSummary {
  id: string;
  name: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({ error: response.statusText }));
    throw new Error((body as { error?: string }).error ?? `request to ${path} failed with ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export function browse(prefix: string, cursor?: string): Promise<BrowseResult> {
  const params = new URLSearchParams({ prefix });
  if (cursor) params.set("cursor", cursor);
  return api(`/api/browse?${params.toString()}`);
}

export function listSymbols(): Promise<{ symbols: string[] }> {
  return api("/api/symbols");
}

export function listYears(symbol: string): Promise<{ years: YearEntry[] }> {
  return api(`/api/symbols/${encodeURIComponent(symbol)}/years`);
}

export function viewFile(
  key: string,
  page: number,
  pageSize: number,
  month?: number,
  day?: number,
  start?: string,
  end?: string,
): Promise<FilePage> {
  const params = new URLSearchParams({ key, page: String(page), pageSize: String(pageSize) });
  if (month) params.set("month", String(month));
  if (day) params.set("day", String(day));
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  return api(`/api/file?${params.toString()}`);
}

/** Which months/days actually have data in this year's file -- drives the
 * "browse by month/day" folder view in FileViewPage without needing
 * day-partitioned storage (see the Worker's readParquetDateIndex). */
export function fileDates(key: string): Promise<DateIndex> {
  return api(`/api/file/dates?key=${encodeURIComponent(key)}`);
}

/** The actual/expected-minutes completeness index tam.marketdata computed
 * at ingest time (see tam.marketdata.completeness), for the year/month/day/
 * range status indicator. Resolves to null (not a thrown error) when the
 * sidecar doesn't exist -- a file ingested before this feature existed, or
 * without pandas_market_calendars installed -- so callers can just omit
 * the badge rather than surface an error for something that isn't one. */
export async function fileCompleteness(key: string): Promise<CompletenessIndex | null> {
  try {
    const index = await api<CompletenessIndex>(`/api/file/completeness?key=${encodeURIComponent(key)}`);
    // An old-schema sidecar (written before the actual_minutes/expected_minutes
    // -> actual_bars/expected_bars rename, before scripts/backfill_completeness.py
    // rewrites it) has no actual_bars/expected_bars fields at all -- rendering a
    // badge computed from `undefined` crashes the whole page (a bare property
    // read, not a NaN), not just this one widget. Treat it the same as a 404:
    // no badge, not a crash.
    if (typeof index.actual_bars !== "number" || typeof index.expected_bars !== "number") return null;
    return index;
  } catch {
    return null;
  }
}

export function csvDownloadUrl(key: string): string {
  return `/api/file/csv?key=${encodeURIComponent(key)}`;
}

export function rawDownloadUrl(key: string): string {
  return `/api/download?key=${encodeURIComponent(key)}`;
}

export interface ExportSelection {
  prefixes?: string[];
  keys?: string[];
}

export function exportUrl(selection: ExportSelection, format: "parquet" | "csv"): string {
  const params = new URLSearchParams({ format });
  (selection.prefixes ?? []).forEach((prefix) => params.append("prefix", prefix));
  (selection.keys ?? []).forEach((key) => params.append("key", key));
  return `/api/export?${params.toString()}`;
}

export function listTokens(): Promise<{ tokens: TokenSummary[] }> {
  return api("/api/tokens");
}

export function createToken(name: string): Promise<{ id: string; name: string; token: string; created_at: string }> {
  return api("/api/tokens", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
}

export function revokeToken(id: string): Promise<void> {
  return api(`/api/tokens/${encodeURIComponent(id)}`, { method: "DELETE" });
}
