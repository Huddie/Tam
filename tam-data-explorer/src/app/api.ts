export interface BrowseResult {
  prefixes: string[];
  objects: Array<{ key: string; size: number; uploaded: string }>;
  truncated: boolean;
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

async function api<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    const body = await response.json().catch(() => ({ error: response.statusText }));
    throw new Error((body as { error?: string }).error ?? `request to ${path} failed with ${response.status}`);
  }
  return response.json();
}

export function browse(prefix: string): Promise<BrowseResult> {
  return api(`/api/browse?prefix=${encodeURIComponent(prefix)}`);
}

export function listSymbols(): Promise<{ symbols: string[] }> {
  return api("/api/symbols");
}

export function listYears(symbol: string): Promise<{ years: YearEntry[] }> {
  return api(`/api/symbols/${encodeURIComponent(symbol)}/years`);
}

export function viewFile(key: string, page: number, pageSize: number): Promise<FilePage> {
  return api(`/api/file?key=${encodeURIComponent(key)}&page=${page}&pageSize=${pageSize}`);
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
