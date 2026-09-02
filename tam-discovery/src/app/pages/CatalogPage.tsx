import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  type Discovery,
  type Project,
  assignProject,
  hideDiscovery,
  listDiscoveries,
  listProjects,
  listTags,
  listTypes,
  renameDiscovery,
} from "../api";
import { ManageMenu } from "../ManageMenu";
import { useSort } from "../useSort";

export function CatalogPage() {
  const [params, setParams] = useSearchParams();
  const [discoveries, setDiscoveries] = useState<Discovery[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [tags, setTags] = useState<string[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const filters = useMemo(
    () => ({
      q: params.get("q") ?? "",
      tag: params.get("tag") ?? "",
      type: params.get("type") ?? "",
      creator: params.get("creator") ?? "",
      project: params.get("project") ?? "",
      sort: params.get("sort") ?? "updated",
      page: params.get("page") ?? "1",
      view: params.get("view") === "grouped" ? "grouped" : "flat",
    }),
    [params]
  );
  const page = Math.max(1, Number(filters.page) || 1);

  useEffect(() => {
    listTags().then((r) => setTags(r.tags)).catch(() => {});
    listTypes().then((r) => setTypes(r.types)).catch(() => {});
    listProjects().then((r) => setProjects(r.projects)).catch(() => {});
  }, []);


  useEffect(() => {
    const { view: _view, ...queryFilters } = filters;
    listDiscoveries(queryFilters)
      .then((r) => {
        setDiscoveries(r.discoveries);
        setHasMore(r.hasMore);
      })
      .catch((e) => setError(String(e)));
  }, [filters]);

  function updateFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    // Any filter/sort change re-scopes the result set -- staying on page 3
    // of a now-different query would either show a confusing gap or a
    // silently-empty page, so any change other than the page itself resets
    // back to page 1.
    if (key !== "page") next.delete("page");
    setParams(next);
  }

  function startRename(discovery: Discovery) {
    setRenamingId(discovery.id);
    setRenameValue(discovery.title);
  }

  function saveRename() {
    if (!renamingId || !renameValue.trim()) return;
    const title = renameValue.trim();
    renameDiscovery(renamingId, title)
      .then(() => {
        setDiscoveries((prev) => prev.map((d) => (d.id === renamingId ? { ...d, title } : d)));
        setRenamingId(null);
      })
      .catch((e) => setError(String(e)));
  }

  function deleteRow(id: string) {
    hideDiscovery(id)
      .then(() => setDiscoveries((prev) => prev.filter((d) => d.id !== id)))
      .catch((e) => setError(String(e)));
  }

  function moveToProject(id: string, projectSlug: string) {
    assignProject(id, projectSlug || null)
      .then(() => {
        const project = projectSlug ? (projects.find((p) => p.slug === projectSlug) ?? null) : null;
        setDiscoveries((prev) =>
          prev.map((d) => (d.id === id ? { ...d, project: project ? { id: project.id, slug: project.slug, name: project.name } : null } : d))
        );
      })
      .catch((e) => setError(String(e)));
  }

  const { sorted, toggleSort, indicator } = useSort<Discovery>(discoveries, (discovery, key) => {
    switch (key) {
      case "title":
        return discovery.title.toLowerCase();
      case "type":
        return discovery.type.toLowerCase();
      case "creator":
        return discovery.created_by.toLowerCase();
      case "updated":
        return discovery.updated_at;
      default:
        return "";
    }
  });

  // "By project" groups the already-sorted, already-fetched (one page's
  // worth of) rows client-side -- one section per project that actually has
  // a visible row here, in the same order as the Projects page, "General"
  // (project: null) always last. Purely a display reorganization: it does
  // NOT re-query with a different page size, so a project whose rows are
  // split across two catalog pages still shows split across two groupings,
  // same caveat sorting already has today.
  const groupedSections = useMemo(() => {
    if (filters.view !== "grouped") return null;
    const byKey = new Map<string, Discovery[]>();
    for (const discovery of sorted) {
      const key = discovery.project?.id ?? "__general__";
      (byKey.get(key) ?? byKey.set(key, []).get(key)!).push(discovery);
    }
    const sections = projects
      .filter((project) => byKey.has(project.id))
      .map((project) => ({ key: project.id, name: project.name, rows: byKey.get(project.id)! }));
    if (byKey.has("__general__")) {
      sections.push({ key: "__general__", name: "General", rows: byKey.get("__general__")! });
    }
    return sections;
  }, [filters.view, sorted, projects]);

  function renderRow(discovery: Discovery) {
    return (
      <tr key={discovery.id}>
        <td>
          {renamingId === discovery.id ? (
            <div className="toolbar" style={{ margin: 0 }}>
              <input
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && saveRename()}
                autoFocus
              />
              <button className="primary" disabled={!renameValue.trim()} onClick={saveRename}>
                Save
              </button>
              <button className="secondary" onClick={() => setRenamingId(null)}>
                Cancel
              </button>
            </div>
          ) : (
            <Link to={`/d/${discovery.name}`}>{discovery.title}</Link>
          )}
        </td>
        <td>{discovery.type}</td>
        <td>
          {discovery.can_manage ? (
            <select value={discovery.project?.slug ?? ""} onChange={(e) => moveToProject(discovery.id, e.target.value)}>
              <option value="">General</option>
              {projects.map((project) => (
                <option key={project.id} value={project.slug}>
                  {project.name}
                </option>
              ))}
            </select>
          ) : discovery.project ? (
            <Link to={`/?project=${encodeURIComponent(discovery.project.slug)}`}>{discovery.project.name}</Link>
          ) : (
            <span className="muted">General</span>
          )}
        </td>
        <td>
          {discovery.tags.map((tag) => (
            <span className="tag" key={tag}>
              {tag}
            </span>
          ))}
        </td>
        <td className="muted">{discovery.created_by}</td>
        <td className="muted mono" title={new Date(discovery.updated_at).toLocaleString()}>
          {new Date(discovery.updated_at).toLocaleDateString()}
        </td>
        <td>
          {discovery.can_manage && <ManageMenu onRename={() => startRename(discovery)} onDelete={() => deleteRow(discovery.id)} />}
        </td>
      </tr>
    );
  }

  function tableHead() {
    return (
      <thead>
        <tr>
          <th className="sortable" onClick={() => toggleSort("title")}>
            Title{indicator("title")}
          </th>
          <th className="sortable" onClick={() => toggleSort("type")}>
            Type{indicator("type")}
          </th>
          <th>Project</th>
          <th>Tags</th>
          <th className="sortable" onClick={() => toggleSort("creator")}>
            Creator{indicator("creator")}
          </th>
          <th className="sortable" onClick={() => toggleSort("updated")}>
            Updated{indicator("updated")}
          </th>
          <th></th>
        </tr>
      </thead>
    );
  }

  return (
    <div className="page page-wide">
      <header className="page-header">
        <h1>Discovery</h1>
        <span>
          <Link to="/settings/projects">Projects</Link> &nbsp; <Link to="/settings/tokens">Personal tokens</Link>
        </span>
      </header>

      <div className="toolbar">
        <input
          placeholder="Search titles..."
          value={filters.q}
          onChange={(e) => updateFilter("q", e.target.value)}
        />
        <select value={filters.project} onChange={(e) => updateFilter("project", e.target.value)}>
          <option value="">All projects</option>
          <option value="general">General (no project)</option>
          {projects.map((project) => (
            <option key={project.id} value={project.slug}>
              {project.name}
            </option>
          ))}
        </select>
        <select value={filters.type} onChange={(e) => updateFilter("type", e.target.value)}>
          <option value="">All types</option>
          {types.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
        <select value={filters.tag} onChange={(e) => updateFilter("tag", e.target.value)}>
          <option value="">All tags</option>
          {tags.map((tag) => (
            <option key={tag} value={tag}>
              {tag}
            </option>
          ))}
        </select>
        <input
          placeholder="Creator email"
          value={filters.creator}
          onChange={(e) => updateFilter("creator", e.target.value)}
        />
        <select value={filters.sort} onChange={(e) => updateFilter("sort", e.target.value)}>
          <option value="updated">Recently updated</option>
          <option value="newest">Newest</option>
        </select>
        <div className="tabs" style={{ marginBottom: 0, borderBottom: "none" }}>
          <button className={filters.view === "flat" ? "active" : ""} onClick={() => updateFilter("view", "")}>
            All discoveries
          </button>
          <button className={filters.view === "grouped" ? "active" : ""} onClick={() => updateFilter("view", "grouped")}>
            By project
          </button>
        </div>
      </div>

      {error && <p className="error">{error}</p>}

      {groupedSections ? (
        groupedSections.map((section) => (
          <div key={section.key} className="project-section">
            <h2>
              {section.name} <span className="muted">({section.rows.length})</span>
            </h2>
            <div className="table-wrap">
              <table>
                {tableHead()}
                <tbody>{section.rows.map(renderRow)}</tbody>
              </table>
            </div>
          </div>
        ))
      ) : (
        <div className="table-wrap">
          <table>
            {tableHead()}
            <tbody>{sorted.map(renderRow)}</tbody>
          </table>
        </div>
      )}
      {discoveries.length === 0 && !error && <p className="muted">No discoveries match these filters.</p>}
      {(page > 1 || hasMore) && (
        <div className="pagination">
          <button className="pager-btn" disabled={page <= 1} onClick={() => updateFilter("page", String(page - 1))}>
            &larr; Previous
          </button>
          <span className="muted">Page {page}</span>
          <button className="pager-btn" disabled={!hasMore} onClick={() => updateFilter("page", String(page + 1))}>
            Next &rarr;
          </button>
        </div>
      )}
    </div>
  );
}
