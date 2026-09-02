export interface ProjectRef {
  id: string;
  slug: string;
  name: string;
}

export interface Discovery {
  id: string;
  name: string;
  type: string;
  title: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  tags: string[];
  project: ProjectRef | null;
  can_manage: boolean;
}

export interface DiscoveryDetail extends Discovery {
  latest_version_id: string;
}

export interface Project {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  discovery_count: number;
  can_manage: boolean;
}

export interface VersionSummary {
  id: string;
  version_number: number;
  title: string;
  description: string | null;
  uploaded_by: string;
  created_at: string;
  git_commit: string | null;
  git_branch: string | null;
  git_repo: string | null;
  git_dirty: number | null;
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

export function listDiscoveries(params: Record<string, string>): Promise<{ discoveries: Discovery[]; page: number; hasMore: boolean }> {
  const query = new URLSearchParams(Object.fromEntries(Object.entries(params).filter(([, value]) => value)));
  return api(`/api/discoveries?${query.toString()}`);
}

export function getDiscovery(idOrSlug: string): Promise<DiscoveryDetail> {
  return api(`/api/discoveries/${encodeURIComponent(idOrSlug)}`);
}

export function getVersions(idOrSlug: string): Promise<{ versions: VersionSummary[] }> {
  return api(`/api/discoveries/${encodeURIComponent(idOrSlug)}/versions`);
}

export function renameDiscovery(idOrSlug: string, title: string): Promise<{ id: string; title: string }> {
  return api(`/api/discoveries/${encodeURIComponent(idOrSlug)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

export function hideDiscovery(idOrSlug: string): Promise<{ id: string; hidden: boolean }> {
  return api(`/api/discoveries/${encodeURIComponent(idOrSlug)}/hide`, { method: "POST" });
}

export function assignProject(idOrSlug: string, project: string | null): Promise<{ id: string; project_id: string | null }> {
  return api(`/api/discoveries/${encodeURIComponent(idOrSlug)}/project`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project }),
  });
}

export function listProjects(): Promise<{ projects: Project[] }> {
  return api("/api/projects");
}

export function createProject(name: string, description?: string): Promise<Project> {
  return api("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, description }),
  });
}

export function updateProject(id: string, fields: { name?: string; description?: string }): Promise<Project> {
  return api(`/api/projects/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });
}

export function archiveProject(id: string): Promise<{ id: string; archived: boolean }> {
  return api(`/api/projects/${encodeURIComponent(id)}/archive`, { method: "POST" });
}

export function listTags(): Promise<{ tags: string[] }> {
  return api("/api/tags");
}

export function listTypes(): Promise<{ types: string[] }> {
  return api("/api/types");
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
