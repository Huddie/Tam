import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { type DiscoveryDetail, type VersionSummary, getDiscovery, getVersions } from "../api";

export function DetailPage() {
  const { id } = useParams<{ id: string }>();
  const [discovery, setDiscovery] = useState<DiscoveryDetail | null>(null);
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!id) return;
    Promise.all([getDiscovery(id), getVersions(id)])
      .then(([discoveryResult, versionsResult]) => {
        setDiscovery(discoveryResult);
        setVersions(versionsResult.versions);
      })
      .catch((e) => setError(String(e)));
  }, [id]);

  if (error) return <p className="error page">{error}</p>;
  if (!discovery) return <p className="muted page">Loading...</p>;

  return (
    <div className="viewer-page">
      {/* No allow-same-origin -- this keeps the artifact's origin opaque and
          unique, so its JS can never reach this app's cookies/session even
          if the artifact itself were malicious. See src/worker/routes/view.ts
          for the matching server-side CSP `sandbox` directive -- the two
          together are what actually enforce this, not either alone. */}
      <iframe title={discovery.title} src={`/d/${id}/view`} sandbox="allow-scripts" className="viewer-frame-full" />

      <div className="detail-overlay">
        <button className="detail-overlay-toggle" onClick={() => setExpanded((value) => !value)}>
          <span>{discovery.title}</span>
          <span className="chevron">{expanded ? "▾" : "▸"}</span>
        </button>

        {expanded && (
          <div className="detail-overlay-body">
            <p>
              <Link to="/">&larr; Back to catalog</Link>
            </p>
            <p className="muted">
              <strong>Type:</strong> {discovery.type} &nbsp; <strong>Created by:</strong> {discovery.created_by}
            </p>
            <p>
              {discovery.tags.length ? (
                discovery.tags.map((tag) => (
                  <span className="tag" key={tag}>
                    {tag}
                  </span>
                ))
              ) : (
                <span className="muted">(no tags)</span>
              )}
            </p>

            <h2>Versions</h2>
            <ul>
              {versions.map((version) => (
                <li key={version.id}>
                  v{version.version_number} -- {version.title} by {version.uploaded_by} on{" "}
                  {new Date(version.created_at).toLocaleString()}
                  {version.git_commit && (
                    <>
                      {" "}
                      (commit <code>{version.git_commit.slice(0, 8)}</code>
                      {version.git_branch ? ` on ${version.git_branch}` : ""}
                      {version.git_dirty ? ", dirty tree" : ""})
                    </>
                  )}
                  {" -- "}
                  <Link to={`/d/${version.id}`}>permalink</Link>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
