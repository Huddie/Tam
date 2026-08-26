import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { type Discovery, listDiscoveries, listTags, listTypes } from "../api";

export function CatalogPage() {
  const [params, setParams] = useSearchParams();
  const [discoveries, setDiscoveries] = useState<Discovery[]>([]);
  const [tags, setTags] = useState<string[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const filters = useMemo(
    () => ({
      q: params.get("q") ?? "",
      tag: params.get("tag") ?? "",
      type: params.get("type") ?? "",
      creator: params.get("creator") ?? "",
      sort: params.get("sort") ?? "updated",
    }),
    [params]
  );

  useEffect(() => {
    listTags().then((r) => setTags(r.tags)).catch(() => {});
    listTypes().then((r) => setTypes(r.types)).catch(() => {});
  }, []);

  useEffect(() => {
    listDiscoveries(filters)
      .then((r) => setDiscoveries(r.discoveries))
      .catch((e) => setError(String(e)));
  }, [filters]);

  function updateFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  }

  return (
    <div className="page">
      <header className="page-header">
        <h1>Discovery</h1>
        <Link to="/settings/tokens">Publishing tokens</Link>
      </header>

      <div className="toolbar">
        <input
          placeholder="Search titles..."
          value={filters.q}
          onChange={(e) => updateFilter("q", e.target.value)}
        />
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
      </div>

      {error && <p className="error">{error}</p>}

      <table>
        <thead>
          <tr>
            <th>Title</th>
            <th>Type</th>
            <th>Tags</th>
            <th>Creator</th>
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>
          {discoveries.map((discovery) => (
            <tr key={discovery.id}>
              <td>
                <Link to={`/d/${discovery.name}`}>{discovery.title}</Link>
              </td>
              <td>{discovery.type}</td>
              <td>
                {discovery.tags.map((tag) => (
                  <span className="tag" key={tag}>
                    {tag}
                  </span>
                ))}
              </td>
              <td className="muted">{discovery.created_by}</td>
              <td className="muted">{new Date(discovery.updated_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {discoveries.length === 0 && !error && <p className="muted">No discoveries match these filters.</p>}
    </div>
  );
}
