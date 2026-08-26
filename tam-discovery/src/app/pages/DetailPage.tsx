import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { type DiscoveryDetail, type VersionSummary, getDiscovery, getVersions, hideDiscovery, renameDiscovery } from "../api";
import { ManageMenu } from "../ManageMenu";
import { Spinner } from "../Spinner";

export function DetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [discovery, setDiscovery] = useState<DiscoveryDetail | null>(null);
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");

  useEffect(() => {
    if (!id) return;
    Promise.all([getDiscovery(id), getVersions(id)])
      .then(([discoveryResult, versionsResult]) => {
        setDiscovery(discoveryResult);
        setVersions(versionsResult.versions);
      })
      .catch((e) => setError(String(e)));
  }, [id]);

  function startRename() {
    if (!discovery) return;
    setRenameValue(discovery.title);
    setRenaming(true);
    setExpanded(true);
  }

  function saveRename() {
    if (!id || !renameValue.trim()) return;
    renameDiscovery(id, renameValue.trim())
      .then(() => {
        setDiscovery((prev) => (prev ? { ...prev, title: renameValue.trim() } : prev));
        setRenaming(false);
      })
      .catch((e) => setError(String(e)));
  }

  function deleteDiscovery() {
    if (!id) return;
    hideDiscovery(id)
      .then(() => navigate("/"))
      .catch((e) => setError(String(e)));
  }

  if (error) return <p className="error page">{error}</p>;
  if (!discovery)
    return (
      <div className="page">
        <Spinner />
      </div>
    );

  return (
    <div className="viewer-page">
      <div className="detail-overlay">
        <div className="detail-overlay-header">
          <button className="detail-overlay-toggle" onClick={() => setExpanded((value) => !value)}>
            <span>{discovery.title}</span>
            <span className="chevron">{expanded ? "▾" : "▸"}</span>
          </button>
          {discovery.can_manage && <ManageMenu onRename={startRename} onDelete={deleteDiscovery} />}
        </div>

        {expanded && (
          <div className="detail-overlay-body">
            <p>
              <Link to="/">&larr; Back to catalog</Link>
            </p>

            {renaming ? (
              <div className="toolbar">
                <input
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && saveRename()}
                  autoFocus
                />
                <button className="primary" disabled={!renameValue.trim()} onClick={saveRename}>
                  Save
                </button>
                <button className="secondary" onClick={() => setRenaming(false)}>
                  Cancel
                </button>
              </div>
            ) : (
              <p className="muted">
                <strong>Type:</strong> {discovery.type} &nbsp; <strong>Created by:</strong> {discovery.created_by}
              </p>
            )}

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

      <div className="viewer-frame-wrap">
        {/* No allow-same-origin -- this keeps the artifact's origin opaque
            and unique, so its JS can never reach this app's cookies/session
            even if the artifact itself were malicious. See
            src/worker/routes/view.ts for the matching server-side CSP
            `sandbox` directive -- the two together are what actually
            enforce this, not either alone. */}
        <iframe title={discovery.title} src={`/d/${id}/view`} sandbox="allow-scripts" className="viewer-frame-full" />
      </div>
    </div>
  );
}
