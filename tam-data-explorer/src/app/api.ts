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

export function listTokens(): Promise<{ tokens: TokenSummary[] }> {
  return api("/api/tokens");
}

export function createToken(name: string): Promise<{ id: string; name: string; token: string; created_at: string }> {
  return api("/api/tokens", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
}

export function revokeToken(id: string): Promise<void> {
  return api(`/api/tokens/${encodeURIComponent(id)}`, { method: "DELETE" });
}
