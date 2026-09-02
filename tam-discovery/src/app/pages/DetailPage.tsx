import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  type DiscoveryDetail,
  type Project,
  type VersionSummary,
  getDiscovery,
  getVersions,
  listProjects,
  listTags,
} from "../api";
import { DiscoveryManageModal } from "../DiscoveryManageModal";
import { KebabIcon } from "../Icons";
import { Spinner } from "../Spinner";

export function DetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [discovery, setDiscovery] = useState<DiscoveryDetail | null>(null);
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [allTags, setAllTags] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [managing, setManaging] = useState(false);

  useEffect(() => {
    if (!id) return;
    Promise.all([getDiscovery(id), getVersions(id)])
      .then(([discoveryResult, versionsResult]) => {
        setDiscovery(discoveryResult);
        setVersions(versionsResult.versions);
      })
      .catch((e) => setError(String(e)));
  }, [id]);

  useEffect(() => {
    listProjects().then((r) => setProjects(r.projects)).catch(() => {});
    listTags().then((r) => setAllTags(r.tags)).catch(() => {});
  }, []);

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
          {discovery.can_manage && (
            <button className="kebab-btn detail-overlay-kebab" aria-label="Manage this discovery" onClick={() => setManaging(true)}>
              <KebabIcon />
            </button>
          )}
        </div>

        {expanded && (
          <div className="detail-overlay-body">
            <p>
              <Link to="/">&larr; Back to catalog</Link>
            </p>

            <p className="muted">
              <strong>Type:</strong> {discovery.type} &nbsp; <strong>Project:</strong>{" "}
              {discovery.project ? (
                <Link to={`/?project=${encodeURIComponent(discovery.project.slug)}`}>{discovery.project.name}</Link>
              ) : (
                "General"
              )}{" "}
              &nbsp; <strong>Created by:</strong> {discovery.created_by}
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

      <div className="viewer-frame-wrap">
        {/* No allow-same-origin -- this keeps the artifact's origin opaque
            and unique, so its JS can never reach this app's cookies/session
            even if the artifact itself were malicious. See
            src/worker/routes/view.ts for the matching server-side CSP
            `sandbox` directive -- the two together are what actually
            enforce this, not either alone. */}
        <iframe title={discovery.title} src={`/d/${id}/view`} sandbox="allow-scripts" className="viewer-frame-full" />
      </div>

      {managing && (
        <DiscoveryManageModal
          discovery={discovery}
          projects={projects}
          allTags={allTags}
          onClose={() => setManaging(false)}
          onRenamed={(title) => setDiscovery((prev) => (prev ? { ...prev, title } : prev))}
          onMoved={(project) => setDiscovery((prev) => (prev ? { ...prev, project } : prev))}
          onTagsChanged={(tags) => setDiscovery((prev) => (prev ? { ...prev, tags } : prev))}
          onDeleted={() => navigate("/")}
        />
      )}
    </div>
  );
}
